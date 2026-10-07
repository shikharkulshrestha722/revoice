# API endpoints, with Colab and Gemini mocked.
import base64

import pytest
import requests
from fastapi.testclient import TestClient

import app as backend

client = TestClient(backend.app)


class FakeResponse:
    """Pretends to be the reply from requests.post()."""
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("not JSON")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


@pytest.fixture
def fake_colab(monkeypatch):
    """Replaces the real Colab call. Records what was sent; replies with
    whatever the test puts in fake_colab["reply"]."""
    record = {"reply": FakeResponse(200, {"audio_base64": "UklGRg=="})}

    def fake_post(url, json=None, headers=None, timeout=None):
        record.update(url=url, payload=json, headers=headers)
        if isinstance(record["reply"], Exception):
            raise record["reply"]
        return record["reply"]

    monkeypatch.setattr(backend.requests, "post", fake_post)
    monkeypatch.setattr(backend, "COLAB_API_URL", "https://colab.test")
    return record


@pytest.fixture(autouse=True)
def tester(store):
    """Most tests need a selected user with a cloned voice."""
    profile = store.create("Tester", "cloned", consent_given=True)
    store.select(profile["id"])
    return profile


def upload(emotion, data=b"fake-webm-audio"):
    return client.post(
        "/upload_voice_clip",
        data={"emotion": emotion},
        files={"file": ("clip.webm", data, "audio/webm")},
    )


# Voice bank

def test_upload_and_count_clips(tester):
    assert upload("happy").json() == {
        "status": "saved", "profile": tester["id"], "emotion": "happy", "clips": 1}
    upload("happy")
    upload("urgent")
    assert client.get("/voice_bank").json() == {"happy": 2, "urgent": 1}


def test_only_last_three_clips_are_kept(store, tester):
    for i in range(5):
        upload("neutral", f"clip{i}".encode())
    assert store.get_clips(tester["id"], "neutral") == [b"clip2", b"clip3", b"clip4"]


def test_upload_unknown_emotion_is_refused():
    r = upload("../../evil")
    assert r.status_code == 400
    assert "Unknown emotion" in r.json()["error"]


# /speak

def test_speak_without_clips_is_refused(fake_colab):
    r = client.post("/speak", data={"text": "HELLO", "emotion": "happy"})
    assert r.status_code == 400
    assert "No voice sample" in r.json()["error"]
    assert "url" not in fake_colab           # Colab was never called


def test_speak_unknown_engine_is_refused(fake_colab):
    upload("neutral")
    r = client.post("/speak", data={"text": "HELLO", "emotion": "neutral", "engine": "robot"})
    assert r.status_code == 400
    assert "Unknown engine" in r.json()["error"]


def test_speak_sends_correct_request_to_colab(fake_colab):
    upload("urgent", b"voice-1")
    upload("urgent", b"voice-2")

    r = client.post("/speak", data={"text": "I NEED HELP", "emotion": "urgent"})

    assert r.status_code == 200
    reply = r.json()
    assert reply["audio_base64"] == "UklGRg=="             # passed straight to the browser
    # ...plus exactly which recordings were sent (shown on the page)
    assert reply["voice_reference"]["source"] == "emotion_clips"
    assert [c["name"] for c in reply["voice_reference"]["clips"]] == ["urgent/000001.webm", "urgent/000002.webm"]
    assert fake_colab["url"] == "https://colab.test/synthesize"
    assert fake_colab["headers"]["ngrok-skip-browser-warning"] == "true"

    p = fake_colab["payload"]
    assert p["text"] == "I need help!"                     # cleaned up for speech
    assert p["emotion"] == "urgent"
    assert p["engine"] == "xtts"                           # default engine
    assert p["language"] == "en"
    assert [base64.b64decode(c) for c in p["speaker_wav_base64_list"]] == [b"voice-1", b"voice-2"]
    assert p["speaker_wav_base64"] is None and p["stock_speaker"] is None    # same fields as Step 4b


def test_speak_forwards_f5_engine(fake_colab):
    upload("neutral")
    client.post("/speak", data={"text": "HELLO", "emotion": "neutral", "engine": "f5"})
    assert fake_colab["payload"]["engine"] == "f5"


def test_speak_handles_trailing_slash_in_colab_url(fake_colab, monkeypatch):
    monkeypatch.setattr(backend, "COLAB_API_URL", "https://colab.test/")
    upload("neutral")
    client.post("/speak", data={"text": "HELLO", "emotion": "neutral"})
    assert fake_colab["url"] == "https://colab.test/synthesize"


def test_speak_reports_colab_error_message(fake_colab):
    fake_colab["reply"] = FakeResponse(500, {"error": "RuntimeError: ffmpeg failed"})
    upload("neutral")
    r = client.post("/speak", data={"text": "HELLO", "emotion": "neutral"})
    assert r.status_code == 502
    assert "ffmpeg failed" in r.json()["error"]


def test_speak_reports_non_json_colab_error(fake_colab):
    fake_colab["reply"] = FakeResponse(404, None, text="ERR_NGROK_3200 endpoint offline")
    upload("neutral")
    r = client.post("/speak", data={"text": "HELLO", "emotion": "neutral"})
    assert r.status_code == 502
    assert "ERR_NGROK_3200" in r.json()["error"]


def test_speak_when_colab_is_unreachable(fake_colab):
    fake_colab["reply"] = requests.exceptions.ConnectionError("connection refused")
    upload("neutral")
    r = client.post("/speak", data={"text": "HELLO", "emotion": "neutral"})
    assert r.status_code == 502
    assert "Could not reach Colab" in r.json()["error"]


# /predict

def test_predict_uses_gemini_when_key_is_set(monkeypatch):
    prompts = []
    monkeypatch.setattr(backend, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(backend, "call_gemini",
                        lambda p: prompts.append(p) or '["I need water", "I need help", "I need rest"]')

    r = client.post("/predict", data={"text": "I NEED "})

    assert r.json() == {"suggestions": ["I need water", "I need help", "I need rest"], "source": "gemini"}
    assert '"I NEED"' in prompts[0]


@pytest.mark.parametrize("key", ["", "PASTE_YOUR_GEMINI_KEY_HERE"])
def test_predict_without_key_is_offline_and_never_calls_gemini(monkeypatch, key):
    def must_not_be_called(prompt):
        raise AssertionError("Gemini was called without a key")
    monkeypatch.setattr(backend, "GEMINI_API_KEY", key)
    monkeypatch.setattr(backend, "call_gemini", must_not_be_called)

    r = client.post("/predict", data={"text": ""})
    assert r.json()["source"] == "offline"
    assert len(r.json()["suggestions"]) == 3


def test_predict_falls_back_when_gemini_fails(monkeypatch):
    def broken(prompt):
        raise requests.exceptions.Timeout("too slow")
    monkeypatch.setattr(backend, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(backend, "call_gemini", broken)

    r = client.post("/predict", data={"text": "I "})
    assert r.json() == {"suggestions": ["I need", "I am", "I want"], "source": "offline"}


def test_call_gemini_sends_key_in_header_and_reads_reply(monkeypatch):
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, headers=headers, body=json)
        return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": '["Yes"]'}]}}]})

    monkeypatch.setattr(backend, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(backend.requests, "post", fake_post)

    assert backend.call_gemini("hello") == '["Yes"]'
    assert sent["headers"]["x-goog-api-key"] == "test-key"
    assert "test-key" not in sent["url"]                  # key never goes in the URL
    assert sent["body"]["contents"][0]["parts"][0]["text"] == "hello"


# No-cache middleware

@pytest.mark.parametrize("path", ["/", "/app.js", "/tracking.js", "/style.css"])
def test_frontend_files_are_not_cached(path):
    r = client.get(path)
    assert r.status_code == 200
    assert "no-store" in r.headers["cache-control"]


def test_api_replies_are_not_given_the_no_cache_header():
    r = client.get("/voice_bank")
    assert "cache-control" not in r.headers


# Retry once on connection problems

def sequence_post(monkeypatch, outcomes):
    """Fake Colab that gives each outcome in turn (an exception, or a reply)."""
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(backend.requests, "post", fake_post)
    monkeypatch.setattr(backend, "COLAB_API_URL", "https://colab.test")
    return calls


@pytest.mark.parametrize("error", [
    requests.exceptions.ConnectionError("connection reset"),
    requests.exceptions.SSLError("EOF occurred in violation of protocol"),
])
def test_speak_retries_once_after_connection_or_ssl_error(monkeypatch, error):
    calls = sequence_post(monkeypatch, [error, FakeResponse(200, {"audio_base64": "ok"})])
    upload("neutral")
    r = client.post("/speak", data={"text": "HELLO", "emotion": "neutral"})
    assert r.status_code == 200 and r.json()["audio_base64"] == "ok"
    assert len(calls) == 2


def test_speak_gives_up_after_second_connection_error(monkeypatch):
    err = requests.exceptions.ConnectionError("down")
    calls = sequence_post(monkeypatch, [err, err])
    upload("neutral")
    r = client.post("/speak", data={"text": "HELLO", "emotion": "neutral"})
    assert r.status_code == 502 and "Could not reach Colab" in r.json()["error"]
    assert len(calls) == 2                      # exactly one retry, not more


def test_colab_error_reply_is_not_retried(monkeypatch):
    calls = sequence_post(monkeypatch, [FakeResponse(500, {"error": "CUDA out of memory"})])
    upload("neutral")
    r = client.post("/speak", data={"text": "HELLO", "emotion": "neutral"})
    assert r.status_code == 502
    assert len(calls) == 1


def test_read_timeout_is_not_retried(monkeypatch):
    # Colab may still be generating: retrying would do the slow work twice
    calls = sequence_post(monkeypatch, [requests.exceptions.ReadTimeout("too slow")])
    upload("neutral")
    assert client.post("/speak", data={"text": "HELLO", "emotion": "neutral"}).status_code == 502
    assert len(calls) == 1


def test_speak_forwards_f5_reference_transcript(fake_colab):
    fake_colab["reply"] = FakeResponse(200, {"audio_base64": "x", "ref_text": "Hello, this is me."})
    upload("neutral")
    r = client.post("/speak", data={"text": "HELLO", "emotion": "neutral", "engine": "f5"})
    assert r.json()["ref_text"] == "Hello, this is me."
