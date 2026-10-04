# Tests for guided voice banking: the sentence list, saving clips with
# their transcripts, resume/skip, references for cloning, and the
# LJSpeech-style dataset export.
import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

import app as backend
import dataset_export
from guided_sentences import GUIDED_SENTENCES, SENTENCE_BY_ID
from profiles import ProfileError

client = TestClient(backend.app)


@pytest.fixture
def user(store):
    p = store.create("Asha", "cloned", consent_given=True)
    store.select(p["id"])
    return p


def record(sentence_id, data=b"audio", duration=3.0, profile_id=""):
    return client.post("/guided/record",
                       data={"sentence_id": sentence_id, "duration_s": str(duration), "profile_id": profile_id},
                       files={"file": ("take.webm", data, "audio/webm")})


# ================= the sentence list =================

def test_there_are_150_sentences_with_unique_ordered_ids():
    ids = [sid for sid, _ in GUIDED_SENTENCES]
    assert len(ids) == 150
    assert len(set(ids)) == 150
    assert ids == [f"g{i:03d}" for i in range(1, 151)]


def test_sentences_are_safe_for_the_dataset_file():
    for sid, text in GUIDED_SENTENCES:
        assert "|" not in text, sid                      # would break metadata.csv columns
        assert not re.search(r"\d", text), sid          # numbers must be spelled out
        assert text == text.strip() and "  " not in text, sid
        assert text[-1] in ".?!", sid
        assert 1 <= len(text.split()) <= 12, sid         # short enough to read in one breath


# ================= storage =================

def test_save_keeps_exact_transcript(store, user):
    store.save_guided(user["id"], "g001", b"take1", ".webm", 3.4)
    manifest = json.loads((store.profiles_dir / user["id"] / "guided" / "manifest.json").read_text())
    rec = manifest["recordings"]["g001"]
    assert rec["text"] == SENTENCE_BY_ID["g001"]
    assert rec["duration_s"] == 3.4
    assert (store.profiles_dir / user["id"] / "guided" / "g001.webm").read_bytes() == b"take1"


def test_redo_replaces_the_previous_take(store, user):
    store.save_guided(user["id"], "g002", b"first", ".webm", 2.0)
    status = store.save_guided(user["id"], "g002", b"second", ".webm", 2.5)
    assert status["count"] == 1
    assert status["total_seconds"] == 2.5
    assert store.guided_audio_path(user["id"], "g002").read_bytes() == b"second"


def test_progress_and_total_time(store, user):
    store.save_guided(user["id"], "g001", b"a", ".webm", 60.0)
    status = store.save_guided(user["id"], "g003", b"b", ".webm", 30.0)
    assert status["count"] == 2
    assert status["total_sentences"] == 150
    assert status["total_seconds"] == 90.0
    assert status["next_id"] == "g002"          # resume at the first gap


def test_skip_moves_on_then_comes_back_at_the_end(store, user):
    assert store.skip_guided(user["id"], "g001")["next_id"] == "g002"
    for sid, _ in GUIDED_SENTENCES[1:]:
        store.save_guided(user["id"], sid, b"x", ".webm", 1.0)
    status = store.guided_status(user["id"])
    assert status["next_id"] == "g001"          # skipped one is offered again
    status = store.save_guided(user["id"], "g001", b"x", ".webm", 1.0)
    assert status["next_id"] is None            # all done
    assert status["skipped"] == []


def test_resume_after_restart(store, user):
    from profiles import ProfileStore
    store.save_guided(user["id"], "g001", b"a", ".webm", 2.0)
    store.skip_guided(user["id"], "g002")
    restarted = ProfileStore(store.data_dir)
    status = restarted.guided_status(user["id"])
    assert status["count"] == 1 and status["next_id"] == "g003"


@pytest.mark.parametrize("duration", [0.0, 0.2, 61.0])
def test_bad_durations_are_refused(store, user, duration):
    with pytest.raises(ProfileError):
        store.save_guided(user["id"], "g001", b"a", ".webm", duration)


def test_unknown_sentence_is_refused(store, user):
    with pytest.raises(ProfileError):
        store.save_guided(user["id"], "../evil", b"a", ".webm", 2.0)


def test_stock_user_cannot_record(store):
    p = store.create("P", "stock", stock_speaker="Ana Florence")
    with pytest.raises(ProfileError):
        store.save_guided(p["id"], "g001", b"a", ".webm", 2.0)


def test_deleting_user_removes_guided_recordings(store, user):
    store.save_guided(user["id"], "g001", b"a", ".webm", 2.0)
    store.delete(user["id"])
    assert not (store.profiles_dir / user["id"]).exists()


# ================= references for cloning =================

def test_pick_references_takes_longest_until_30_seconds(store, user):
    for sid, secs in [("g001", 4.0), ("g002", 12.0), ("g003", 9.0), ("g004", 11.0), ("g005", 2.0)]:
        store.save_guided(user["id"], sid, sid.encode(), ".webm", secs)
    chosen = store.pick_references(user["id"])
    assert chosen == ["g002", "g004", "g003"]          # 12 + 11 + 9 = 32 s
    assert store.get_reference_clips(user["id"]) == [b"g002", b"g004", b"g003"]


def test_pick_references_caps_the_number_of_clips(store, user):
    for sid, _ in GUIDED_SENTENCES[:15]:
        store.save_guided(user["id"], sid, b"x", ".webm", 1.0)
    assert len(store.pick_references(user["id"])) == 10


def test_pick_references_needs_recordings(store, user):
    with pytest.raises(ProfileError):
        store.pick_references(user["id"])


def test_speak_falls_back_to_guided_references(store, user, monkeypatch):
    sent = {}
    monkeypatch.setattr(backend.requests, "post",
                        lambda url, json=None, **kw: sent.update(json) or FakeOk())
    store.save_guided(user["id"], "g001", b"guided-voice", ".webm", 5.0)

    # no emotion clips and no references yet -> helpful error
    r = client.post("/speak", data={"text": "HI", "emotion": "happy"})
    assert r.status_code == 400 and "guided" in r.json()["error"]

    client.post("/guided/references")
    r = client.post("/speak", data={"text": "HI", "emotion": "happy"})
    assert r.status_code == 200
    import base64
    assert [base64.b64decode(c) for c in sent["speaker_wav_base64_list"]] == [b"guided-voice"]


def test_emotion_clips_win_over_guided_references(store, user, monkeypatch):
    sent = {}
    monkeypatch.setattr(backend.requests, "post",
                        lambda url, json=None, **kw: sent.update(json) or FakeOk())
    store.save_guided(user["id"], "g001", b"guided", ".webm", 5.0)
    store.pick_references(user["id"])
    store.add_clip(user["id"], "happy", b"happy-clip")
    client.post("/speak", data={"text": "HI", "emotion": "happy"})
    import base64
    assert [base64.b64decode(c) for c in sent["speaker_wav_base64_list"]] == [b"happy-clip"]


class FakeOk:
    status_code = 200
    text = ""

    def json(self):
        return {"audio_base64": "x"}


# ================= endpoints =================

def test_sentences_endpoint():
    data = client.get("/guided/sentences").json()["sentences"]
    assert data[0] == {"id": "g001", "text": SENTENCE_BY_ID["g001"]}
    assert len(data) == 150


def test_record_status_skip_and_playback_endpoints(user):
    assert client.get("/guided/status").json()["next_id"] == "g001"
    r = record("g001", b"hello", 2.5)
    assert r.status_code == 200 and r.json()["count"] == 1
    assert client.post("/guided/skip", data={"sentence_id": "g002"}).json()["next_id"] == "g003"

    audio = client.get("/guided/audio/g001")
    assert audio.status_code == 200
    assert audio.content == b"hello"
    assert audio.headers["content-type"].startswith("audio/webm")
    assert client.get("/guided/audio/g005").status_code == 404


def test_record_bad_sentence_is_400(user):
    assert record("nope").status_code == 400


def test_clear_references_endpoint(store, user):
    store.save_guided(user["id"], "g001", b"a", ".webm", 5.0)
    assert client.post("/guided/references").json()["references"] == ["g001"]
    assert client.delete("/guided/references").json()["references"] == []


# ================= dataset export =================

def read_zip(content):
    return zipfile.ZipFile(io.BytesIO(content))


def test_export_without_ffmpeg_gives_raw_clips_and_metadata(store, user, monkeypatch):
    monkeypatch.setattr(dataset_export, "ffmpeg_available", lambda: False)
    store.save_guided(user["id"], "g002", b"two", ".webm", 2.0)
    store.save_guided(user["id"], "g001", b"one", ".webm", 3.0)

    r = client.get(f"/profiles/{user['id']}/export")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f"revoice_dataset_{user['id']}.zip" in r.headers["content-disposition"]
    z = read_zip(r.content)
    assert sorted(z.namelist()) == ["README.txt", "metadata.csv", "raw/g001.webm", "raw/g002.webm"]
    assert z.read("raw/g001.webm") == b"one"

    lines = z.read("metadata.csv").decode("utf-8").split("\n")
    assert lines[-1] == ""                      # ends with a newline, no "\r"
    assert b"\r" not in z.read("metadata.csv")
    t1 = SENTENCE_BY_ID["g001"]
    assert lines[0] == f"g001|{t1}|{t1}"        # in sentence order, 3 columns, no header
    assert lines[1].startswith("g002|")
    readme = z.read("README.txt").decode()
    assert "Asha" in readme and "convert" in readme


def test_export_with_ffmpeg_puts_wavs_in_wavs_folder(store, user, monkeypatch):
    converted = []

    def fake_convert(src, dst):
        converted.append(src.name)
        dst.write_bytes(b"RIFF-fake-wav")

    monkeypatch.setattr(dataset_export, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(dataset_export, "convert_to_wav", fake_convert)
    store.save_guided(user["id"], "g001", b"one", ".webm", 3.0)

    z = read_zip(client.get(f"/profiles/{user['id']}/export").content)
    assert "wavs/g001.wav" in z.namelist()
    assert not any(n.startswith("raw/") for n in z.namelist())
    assert converted == ["g001.webm"]


def test_export_with_nothing_recorded_is_400(user):
    assert client.get(f"/profiles/{user['id']}/export").status_code == 400


def test_export_unknown_user_is_404():
    assert client.get("/profiles/nobody-0000/export").status_code == 404
