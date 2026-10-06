# Tests for the "Voice reference" setting: which recordings /speak copies the voice from,
# and that the page + log are told EXACTLY which ones were sent.
# (Bug this guards against: an uploaded recording was silently ignored because the
# user also had recorded clips, so the website's voice was copied from other audio.)
import base64
import csv
import io
import wave

import pytest
from fastapi.testclient import TestClient

import app as backend
from profiles import ProfileError, clip_duration, webm_duration

client = TestClient(backend.app)


def make_wav(seconds, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def make_webm(block_times_ms):
    """A tiny WebM shaped like Chrome's MediaRecorder output: no duration in the
    header, clusters of unknown size, one SimpleBlock per given time (ms)."""
    def element(eid: bytes, body: bytes) -> bytes:
        return eid + bytes([0x80 | len(body)]) + body if len(body) < 127 else \
            eid + bytes([0x40 | (len(body) >> 8), len(body) & 0xFF]) + body
    unknown = b"\x01\xff\xff\xff\xff\xff\xff\xff"
    out = element(b"\x1a\x45\xdf\xa3", element(b"\x42\x82", b"webm"))           # EBML header
    out += b"\x18\x53\x80\x67" + unknown                                          # Segment
    out += element(b"\x15\x49\xa9\x66", element(b"\x2a\xd7\xb1", (1_000_000).to_bytes(3, "big")))
    cluster_start = None
    for t in block_times_ms:
        if cluster_start is None or t - cluster_start > 30000:                    # new cluster
            cluster_start = t
            out += b"\x1f\x43\xb6\x75" + unknown + element(b"\xe7", t.to_bytes(4, "big"))
        out += element(b"\xa3", b"\x81" + (t - cluster_start).to_bytes(2, "big", signed=True) + b"\x80" + b"o" * 10)
    return out


class FakeOk:
    status_code = 200

    def __init__(self, reply=None):
        self.reply = reply or {"audio_base64": "x"}

    def json(self):
        return self.reply


@pytest.fixture
def user(store):
    p = store.create("Asha", "cloned", consent_given=True)
    store.select(p["id"])
    return p


@pytest.fixture
def colab(monkeypatch):
    """Records what would be sent to Colab."""
    sent = {"calls": 0, "reply": {"audio_base64": "x"}}

    def fake_post(url, json=None, **kw):
        sent["calls"] += 1
        sent["payload"] = json
        return FakeOk(sent["reply"])
    monkeypatch.setattr(backend.requests, "post", fake_post)
    return sent


def add_upload(store, user, seconds=10.0, name="Recording (2).m4a"):
    return store.add_upload(user["id"], make_wav(seconds), original_name=name, start_s=0, end_s=seconds,
                            speech_s=seconds * 0.8, consent=True, consented_by="Ravi")


def sent_clips(colab):
    return [base64.b64decode(c) for c in colab["payload"]["speaker_wav_base64_list"]]


def speak(**fields):
    data = {"text": "HELLO", "emotion": "neutral", "engine": "f5"}
    data.update(fields)
    return client.post("/speak", data=data)


def set_mode(user, mode):
    return client.post(f"/profiles/{user['id']}/voice_reference", data={"mode": mode})


# ================= the setting itself =================

def test_new_and_old_profiles_default_to_auto(store, user):
    assert store.get(user["id"])["voice_reference"] == "auto"


@pytest.mark.parametrize("mode", ["auto", "uploads", "recorded", "guided"])
def test_setting_is_saved(store, user, mode):
    assert set_mode(user, mode).json()["voice_reference"] == mode
    assert store.get(user["id"])["voice_reference"] == mode       # survives a restart (on disk)


def test_unknown_setting_is_refused(user):
    r = set_mode(user, "everything")
    assert r.status_code == 400
    assert "Unknown voice reference" in r.json()["error"]


# ================= the bug: uploads + recorded clips =================

def test_uploads_only_never_sends_recorded_clips(store, user, colab):
    add_upload(store, user, 18.0)
    store.add_clip(user["id"], "neutral", b"mic-clip-1")
    store.add_clip(user["id"], "neutral", b"mic-clip-2")
    set_mode(user, "uploads")

    r = speak()
    assert r.status_code == 200
    wav = (store._uploads_dir(user["id"]) / "000001.wav").read_bytes()
    assert sent_clips(colab) == [wav]                              # ONLY the uploaded clip
    ref = r.json()["voice_reference"]
    assert ref["mode"] == "uploads" and ref["source"] == "uploaded_recordings"
    assert ref["clips"] == [{"source": "uploaded_recordings", "name": "Recording (2).m4a (#000001)",
                             "duration_s": 18.0}]


def test_uploads_only_without_uploads_is_an_error_not_a_swap(store, user, colab):
    store.add_clip(user["id"], "neutral", b"mic-clip")
    set_mode(user, "uploads")
    r = speak()
    assert r.status_code == 400
    assert "Uploaded recordings only" in r.json()["error"]
    assert "no uploaded recordings" in r.json()["error"]
    assert colab["calls"] == 0                                     # nothing else was sent instead


def test_auto_still_prefers_recorded_clips_but_says_so(store, user, colab):
    add_upload(store, user)
    store.add_clip(user["id"], "neutral", b"mic-clip")
    r = speak()
    assert sent_clips(colab) == [b"mic-clip"]
    assert r.json()["voice_reference"]["source"] == "emotion_clips"
    assert r.json()["voice_reference"]["clips"][0]["name"] == "neutral/000001.webm"


def test_auto_uses_uploads_when_no_clip_for_this_emotion(store, user, colab):
    add_upload(store, user)
    store.add_clip(user["id"], "happy", b"happy-clip")             # other emotion: not used
    r = speak(emotion="neutral")
    assert r.json()["voice_reference"]["source"] == "uploaded_recordings"


def test_recorded_only_uses_this_emotions_clips_and_never_uploads(store, user, colab):
    add_upload(store, user)
    set_mode(user, "recorded")
    r = speak(emotion="urgent")
    assert r.status_code == 400 and "no recorded clip for the emotion 'urgent'" in r.json()["error"]
    store.add_clip(user["id"], "urgent", b"urgent-clip")
    assert speak(emotion="urgent").status_code == 200
    assert sent_clips(colab) == [b"urgent-clip"]


def test_guided_only_uses_the_chosen_guided_takes(store, user, colab):
    store.add_clip(user["id"], "neutral", b"mic-clip")
    add_upload(store, user)
    set_mode(user, "guided")
    assert speak().status_code == 400                              # none chosen yet
    store.save_guided(user["id"], "g001", b"take-1", ".webm", 3.0)
    store.save_guided(user["id"], "g002", b"take-2", ".webm", 5.0)
    store.pick_references(user["id"])
    r = speak(engine="xtts")
    assert sent_clips(colab) == [b"take-2", b"take-1"]            # longest first; XTTS uses all
    clips = r.json()["voice_reference"]["clips"]
    assert [(c["source"], c["name"], c["duration_s"]) for c in clips] == [
        ("guided_references", "g002.webm", 5.0), ("guided_references", "g001.webm", 3.0)]
    speak(engine="f5")
    assert sent_clips(colab) == [b"take-2"]                       # F5 copies one clip: the longest


def test_stock_voice_ignores_the_setting(store, colab):
    p = store.create("S", "stock", stock_speaker="Ana Florence")
    store.select(p["id"])
    r = speak(engine="xtts")
    assert r.status_code == 200
    assert "voice_reference" not in r.json()
    assert colab["payload"]["stock_speaker"] == "Ana Florence"


# ================= preview (shown in the Voice Bank before generating) =================

def test_preview_matches_what_speak_sends(store, user, colab):
    add_upload(store, user, 12.5)
    store.add_clip(user["id"], "neutral", b"mic-clip")
    set_mode(user, "uploads")
    preview = client.get("/voice_bank/references", params={"emotion": "neutral"}).json()
    assert preview == speak().json()["voice_reference"]
    assert preview["total_seconds"] == 12.5


def test_preview_explains_a_setting_that_cant_be_met(store, user):
    set_mode(user, "guided")
    preview = client.get("/voice_bank/references", params={"emotion": "neutral"}).json()
    assert preview["clips"] == [] and "Guided takes only" in preview["error"]


# ================= clip lengths =================

def test_webm_length_read_from_the_last_audio_block():
    times = list(range(0, 40000, 20))                              # 40 s of 20 ms Opus frames, 2 clusters
    assert webm_duration(make_webm(times)) == 40.0                 # last block 39.98 s + one 20 ms frame


def test_damaged_or_empty_webm_gives_unknown_length():
    assert webm_duration(b"") is None
    assert webm_duration(b"\x00\x00\x00 not webm") is None
    assert webm_duration(make_webm([0, 20, 40])[:-5]) == 0.06      # cut short: uses what is there


def test_clip_duration_for_wav_and_webm(tmp_path):
    (tmp_path / "a.wav").write_bytes(make_wav(2.5))
    (tmp_path / "b.webm").write_bytes(make_webm([0, 1000, 1980]))
    (tmp_path / "c.mp3").write_bytes(b"ID3...")
    assert clip_duration(tmp_path / "a.wav") == 2.5
    assert clip_duration(tmp_path / "b.webm") == 2.0
    assert clip_duration(tmp_path / "c.mp3") is None               # unknown: shown as "?"


def test_recorded_clip_length_is_shown(store, user, colab):
    store.add_clip(user["id"], "neutral", make_webm(list(range(0, 19920, 20))))
    assert speak().json()["voice_reference"]["clips"][0]["duration_s"] == 19.92


# ================= generation log =================

def log_rows():
    with open(backend.GENERATION_LOG, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_log_records_reference_files_lengths_and_f5_transcript(store, user, colab):
    add_upload(store, user, 18.0)
    add_upload(store, user, 6.0, name="voice note.ogg")
    set_mode(user, "uploads")
    colab["reply"] = {"audio_base64": "x", "ref_text": "  Hello,\nthis is me.  "}
    speak(engine="xtts")
    row = log_rows()[-1]
    assert row["voice_source"] == "uploaded_recordings"
    assert row["ref_mode"] == "uploads"
    assert row["ref_files"] == "Recording (2).m4a (#000001) 18.0s | voice note.ogg (#000002) 6.0s"
    assert row["ref_seconds"] == "24.0"
    assert row["ref_text"] == "Hello, this is me."


def test_log_records_the_refused_setting(store, user, colab):
    set_mode(user, "uploads")
    speak()
    row = log_rows()[-1]
    assert row["success"] == "no" and "Uploaded recordings only" in row["error"]
    assert row["ref_files"] == ""


def test_choose_references_error_is_a_profile_error(store, user):
    store.set_voice_reference(user["id"], "recorded")
    with pytest.raises(ProfileError):
        store.choose_references(user["id"], "neutral")


# ================= one chosen upload, and F5 gets exactly one clip =================

def test_a_specific_uploaded_clip_can_be_chosen(store, user, colab):
    add_upload(store, user, 29.4, name="Recording (3).m4a")      # more speech: "Auto" would pick it
    add_upload(store, user, 18.0, name="Recording (2).m4a")
    r = client.post(f"/profiles/{user['id']}/voice_reference", data={"mode": "uploads", "clip_id": "000002"})
    assert r.json()["reference_clip"] == "000002"
    for engine in ("f5", "xtts"):
        reply = speak(engine=engine).json()
        assert [c["name"] for c in reply["voice_reference"]["clips"]] == ["Recording (2).m4a (#000002)"]
        assert sent_clips(colab) == [(store._uploads_dir(user["id"]) / "000002.wav").read_bytes()]


def test_choosing_a_clip_needs_uploads_mode_and_an_existing_clip(store, user):
    add_upload(store, user)
    r = client.post(f"/profiles/{user['id']}/voice_reference", data={"mode": "auto", "clip_id": "000001"})
    assert r.status_code == 400
    r = client.post(f"/profiles/{user['id']}/voice_reference", data={"mode": "uploads", "clip_id": "000009"})
    assert r.status_code == 404
    # choosing the mode again without a clip goes back to "all uploads"
    client.post(f"/profiles/{user['id']}/voice_reference", data={"mode": "uploads", "clip_id": "000001"})
    assert set_mode(user, "uploads").json()["reference_clip"] == ""


def test_deleted_chosen_clip_is_an_error_never_another_upload(store, user, colab):
    add_upload(store, user, 29.4, name="Recording (3).m4a")
    add_upload(store, user, 18.0, name="Recording (2).m4a")
    store.set_voice_reference(user["id"], "uploads", "000002")
    client.delete(f"/uploads/000002")
    r = speak()
    assert r.status_code == 400 and "no longer exists" in r.json()["error"]
    assert colab["calls"] == 0


def test_f5_gets_one_clip_so_similarity_is_measured_on_what_it_copied(store, user, colab):
    add_upload(store, user, 10.0, name="weak.ogg")                # speech 8 s
    add_upload(store, user, 29.4, name="Recording (3).m4a")       # speech 23.5 s
    set_mode(user, "uploads")
    reply = speak(engine="f5").json()
    assert [c["name"] for c in reply["voice_reference"]["clips"]] == ["Recording (3).m4a (#000002)"]
    assert len(colab["payload"]["speaker_wav_base64_list"]) == 1
    speak(engine="xtts")
    assert len(colab["payload"]["speaker_wav_base64_list"]) == 2   # XTTS really uses them all


def test_f5_with_recorded_clips_uses_the_longest(store, user, colab):
    store.add_clip(user["id"], "neutral", make_webm(list(range(0, 8000, 20))))
    long_clip = make_webm(list(range(0, 19920, 20)))
    store.add_clip(user["id"], "neutral", long_clip)
    speak(engine="f5")
    assert sent_clips(colab) == [long_clip]


def test_website_sends_the_same_fields_as_step_4b(store, user, colab):
    from parity_check import TEST_SENTENCE
    add_upload(store, user)
    set_mode(user, "uploads")
    speak(text=TEST_SENTENCE, emotion="neutral", engine="f5")
    p = colab["payload"]
    wav = (store._uploads_dir(user["id"]) / "000001.wav").read_bytes()
    assert p == {"text": TEST_SENTENCE, "language": "en", "emotion": "neutral", "engine": "f5",
                 "stock_speaker": None, "speaker_wav_base64": None,
                 "speaker_wav_base64_list": [base64.b64encode(wav).decode()]}


def test_preview_shows_what_each_engine_gets(store, user):
    add_upload(store, user, 10.0, name="a.ogg")
    add_upload(store, user, 20.0, name="b.ogg")
    set_mode(user, "uploads")
    f5 = client.get("/voice_bank/references", params={"engine": "f5"}).json()
    xtts = client.get("/voice_bank/references", params={"engine": "xtts"}).json()
    assert [c["name"] for c in f5["clips"]] == ["b.ogg (#000002)"]
    assert len(xtts["clips"]) == 2
