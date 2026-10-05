# Tests for uploading old recordings (for people who already lost their voice).
# The browser sends a trimmed mono WAV; the server checks consent, file type,
# size and trim before saving it.
import base64
import io
import json
import wave

import pytest
from fastapi.testclient import TestClient

import app as backend
from profiles import MAX_UPLOAD_BYTES, ProfileError, UPLOAD_CONSENT_STATEMENT, wav_duration

client = TestClient(backend.app)


def make_wav(seconds=5.0, rate=24000, channels=1, width=2):
    """A silent WAV of the given length (what the browser would send)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * width * channels * int(seconds * rate))
    return buf.getvalue()


@pytest.fixture
def user(store):
    p = store.create("Asha", "cloned", consent_given=True)
    store.select(p["id"])
    return p


def upload(wav=None, **fields):
    data = {"original_name": "voice_note.ogg", "start_s": "2.0", "end_s": "7.0", "speech_s": "4.2",
            "consent": "true", "consented_by": "Ravi (son)"}
    data.update(fields)
    return client.post("/uploads", data=data,
                       files={"file": ("clip.wav", make_wav(5.0) if wav is None else wav, "audio/wav")})


# ================= allowed formats =================

@pytest.mark.parametrize("name", ["note.mp3", "note.m4a", "note.wav", "PTT-2024.OGG", "note.opus",
                                  "clip.webm", "video.mp4"])
def test_allowed_original_formats(user, name):
    r = upload(original_name=name)
    assert r.status_code == 200, r.json()
    assert r.json()["saved"]["original_name"] == name


@pytest.mark.parametrize("name", ["document.pdf", "song.flac", "virus.exe", "noextension", ""])
def test_other_formats_are_refused(user, name):
    r = upload(original_name=name)
    assert r.status_code == 400
    assert "not a supported file type" in r.json()["error"]


def test_audio_must_be_mono_16bit_wav(user):
    assert upload(wav=b"this is not audio at all").status_code == 400
    r = upload(wav=make_wav(5.0, channels=2))
    assert r.status_code == 400 and "mono 16-bit" in r.json()["error"]
    assert upload(wav=make_wav(5.0, width=1)).status_code == 400
    assert upload(wav=make_wav(5.0, rate=96000)).status_code == 400


# ================= consent =================

@pytest.mark.parametrize("consent, name", [("false", "Ravi"), ("true", ""), ("true", "   "), ("", "Ravi")])
def test_consent_and_name_are_required(user, consent, name):
    r = upload(consent=consent, consented_by=name)
    assert r.status_code == 400
    assert "Consent is required" in r.json()["error"]


def test_who_consented_and_when_is_stored(store, user):
    upload(consented_by="  Ravi   (son) ")
    manifest = json.loads((store.profiles_dir / user["id"] / "uploads" / "manifest.json").read_text())
    clip = manifest["clips"][0]
    assert clip["consented_by"] == "Ravi (son)"
    assert clip["consent_statement"] == UPLOAD_CONSENT_STATEMENT
    assert clip["consented_at"].startswith("20")           # ISO date-time


# ================= size limit =================

def test_size_limit(user):
    too_big = make_wav(5.0) + b"\x00" * (MAX_UPLOAD_BYTES + 10)
    r = upload(wav=too_big)
    assert r.status_code == 400
    assert "too big" in r.json()["error"]


# ================= trim validation =================

@pytest.mark.parametrize("start, end, why", [
    ("5", "5", "after"),            # zero length
    ("7", "2", "after"),            # end before start
    ("-1", "4", "after"),           # negative start
    ("0", "0.5", "seconds long"),   # shorter than 1 s
    ("0", "130", "seconds long"),   # longer than 2 minutes
])
def test_bad_trims_are_refused(user, start, end, why):
    r = upload(start_s=start, end_s=end)
    assert r.status_code == 400
    assert why in r.json()["error"]


def test_trim_must_match_the_audio_length(user):
    r = upload(wav=make_wav(5.0), start_s="0", end_s="20")    # says 20 s, sends 5 s
    assert r.status_code == 400
    assert "doesn't match" in r.json()["error"]


def test_speech_time_cannot_exceed_clip(user):
    assert upload(speech_s="9").status_code == 400
    assert upload(speech_s="-1").status_code == 400


def test_trim_and_durations_are_saved(user):
    clip = upload(start_s="2.0", end_s="7.0", speech_s="4.2").json()["saved"]
    assert (clip["trim_start_s"], clip["trim_end_s"], clip["duration_s"], clip["speech_s"]) == (2.0, 7.0, 5.0, 4.2)


def test_non_number_trim_is_rejected(user):
    assert upload(start_s="soon").status_code == 422      # FastAPI refuses it before our code


# ================= list, totals, delete, limits =================

def test_totals_and_enough_speech_warning(user):
    r = upload(speech_s="4.0").json()
    assert r["total_speech_seconds"] == 4.0 and r["enough_speech"] is False
    assert r["min_total_speech_seconds"] == 15.0
    for _ in range(3):
        r = upload(speech_s="4.0").json()
    assert r["total_speech_seconds"] == 16.0 and r["enough_speech"] is True
    assert r["total_seconds"] == 20.0
    assert client.get("/uploads").json()["total_speech_seconds"] == 16.0


def test_delete_one_clip(store, user):
    first = upload().json()["saved"]["id"]
    upload()
    left = client.delete(f"/uploads/{first}").json()
    assert [c["id"] for c in left["clips"]] == ["000002"]
    assert not (store.profiles_dir / user["id"] / "uploads" / f"{first}.wav").exists()
    assert client.delete("/uploads/999999").status_code == 404


def test_max_clips_per_user(store, user, monkeypatch):
    monkeypatch.setattr("profiles.MAX_UPLOADED_CLIPS", 2)
    upload(); upload()
    r = upload()
    assert r.status_code == 400 and "Delete some first" in r.json()["error"]


def test_stock_user_cannot_upload(store):
    p = store.create("P", "stock", stock_speaker="Ana Florence")
    store.select(p["id"])
    assert upload().status_code == 400


def test_deleting_the_user_removes_uploads(store, user):
    upload()
    store.delete(user["id"])
    assert not (store.profiles_dir / user["id"]).exists()


def test_wav_duration_helper():
    assert wav_duration(make_wav(2.5, rate=16000)) == 2.5
    with pytest.raises(ProfileError):
        wav_duration(b"RIFF....nope")


# ================= used as the voice for BOTH engines =================

class FakeOk:
    status_code = 200
    text = ""

    def json(self):
        return {"audio_base64": "x"}


@pytest.mark.parametrize("engine", ["xtts", "f5"])
def test_uploads_are_the_voice_when_there_are_no_emotion_clips(store, user, monkeypatch, engine):
    sent = {}
    monkeypatch.setattr(backend.requests, "post", lambda url, json=None, **kw: sent.update(json) or FakeOk())
    wav = make_wav(5.0)
    upload(wav=wav)
    r = client.post("/speak", data={"text": "HELLO", "emotion": "happy", "engine": engine})
    assert r.status_code == 200
    assert sent["engine"] == engine
    assert [base64.b64decode(c) for c in sent["speaker_wav_base64_list"]] == [wav]


def test_emotion_clips_still_win_over_uploads(store, user, monkeypatch):
    sent = {}
    monkeypatch.setattr(backend.requests, "post", lambda url, json=None, **kw: sent.update(json) or FakeOk())
    upload()
    store.add_clip(user["id"], "neutral", b"fresh-clip")
    client.post("/speak", data={"text": "HELLO", "emotion": "neutral"})
    assert [base64.b64decode(c) for c in sent["speaker_wav_base64_list"]] == [b"fresh-clip"]


def test_most_speech_first_and_request_size_capped(store, user):
    for speech in ["1.0", "4.5", "3.0"]:
        upload(speech_s=speech, wav=make_wav(5.0))
    clips = store._read_uploads(user["id"])["clips"]
    order = sorted(clips, key=lambda c: c["speech_s"], reverse=True)
    assert [c["speech_s"] for c in order] == [4.5, 3.0, 1.0]
    assert len(store.get_upload_clips(user["id"], target_seconds=8)) == 2      # 5 s + 5 s >= 8 s
    assert len(store.get_upload_clips(user["id"], max_clips=1)) == 1
