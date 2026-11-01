# /speak with IndicF5 (and F5 on the same Indian-language text, to compare). Colab and Gemini mocked.
import csv

import pytest
from fastapi.testclient import TestClient

import app as backend
from tests.test_api import FakeResponse, upload

client = TestClient(backend.app)

COLAB_REPLY = {"audio_base64": "UklGRg==", "ref_text": "Hello there.", "synth_seconds": 16.8,
               "metrics": {"similarity": 0.78, "accent_match": None,
                           "reference": {"accent_scores": [["us", 0.4], ["england", 0.2]], "speech_seconds": 9},
                           "output": {"accent_scores": [["indian", 0.3], ["us", 0.1]], "speech_seconds": 5}}}


@pytest.fixture
def colab(monkeypatch):
    """Fake Colab (and, if a test sets gemini_reply, a fake Gemini) behind requests.post."""
    record = {"colab_calls": [], "gemini_calls": [], "gemini_reply": None}

    def fake_post(url, json=None, headers=None, timeout=None):
        if "generativelanguage" in url:
            record["gemini_calls"].append(json)
            if record["gemini_reply"] is None:
                raise AssertionError("Gemini was not expected")
            return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": record["gemini_reply"]}]}}]})
        record["colab_calls"].append({"payload": json, "timeout": timeout})
        return FakeResponse(200, {**COLAB_REPLY, "metrics": {
            **COLAB_REPLY["metrics"], "output": dict(COLAB_REPLY["metrics"]["output"])}})

    monkeypatch.setattr(backend.requests, "post", fake_post)
    monkeypatch.setattr(backend, "COLAB_API_URL", "https://colab.test")
    monkeypatch.setattr(backend, "GEMINI_API_KEY", "test-key")
    return record


@pytest.fixture(autouse=True)
def voice(store):
    profile = store.create("Tester", "cloned", consent_given=True)
    store.select(profile["id"])
    upload("neutral", b"clip-1")
    upload("neutral", b"clip-2")
    return profile


def speak(**fields):
    data = {"text": "main paani chahta hoon", "emotion": "neutral", "engine": "indicf5", "language": "hi"}
    data.update(fields)
    return client.post("/speak", data=data)


def log_rows():
    with open(backend.GENERATION_LOG, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_indicf5_gets_converted_text_language_and_one_clip(colab):
    colab["gemini_reply"] = "मैं पानी चाहता हूँ"
    r = speak(text_mode="transliterate")
    assert r.status_code == 200
    reply = r.json()
    assert reply["converted_text"] == "मैं पानी चाहता हूँ।"        # shown above the player
    assert reply["language"] == "hi" and reply["text_mode"] == "transliterate"

    call = colab["colab_calls"][0]
    p = call["payload"]
    assert p["engine"] == "indicf5" and p["language"] == "hi"
    assert p["text"] == "मैं पानी चाहता हूँ।"
    assert len(p["speaker_wav_base64_list"]) == 1                 # one clip, like F5
    assert call["timeout"] == 300                                 # first request also loads the model
    assert "romanised" in colab["gemini_calls"][0]["contents"][0]["parts"][0]["text"]


def test_text_typed_in_the_script_skips_gemini(colab):
    r = speak(text="मुझे पानी चाहिए", text_mode="translate")
    assert r.status_code == 200
    assert r.json()["text_mode"] == "as_typed"
    assert colab["gemini_calls"] == []
    assert colab["colab_calls"][0]["payload"]["text"] == "मुझे पानी चाहिए।"


def test_without_gemini_english_is_refused_and_colab_is_never_called(colab, monkeypatch):
    monkeypatch.setattr(backend, "GEMINI_API_KEY", "")
    r = speak(text="I need water", text_mode="translate")
    assert r.status_code == 400
    assert "Devanagari" in r.json()["error"]
    assert colab["colab_calls"] == []
    assert log_rows()[-1]["success"] == "no"


def test_gemini_answering_in_english_is_refused(colab):
    colab["gemini_reply"] = "I need water"
    r = speak(text="I need water", text_mode="translate")
    assert r.status_code == 400 and "nothing was spoken" in r.json()["error"]
    assert colab["colab_calls"] == []


def test_compare_sends_the_same_converted_text_to_f5_without_converting_again(colab):
    colab["gemini_reply"] = "मुझे पानी चाहिए"
    first = speak(text="I need water", text_mode="translate", utterance_id="u1", selections="4").json()
    second = speak(text="I need water", engine="f5", text_mode=first["text_mode"],
                   converted_text=first["converted_text"], utterance_id="u1", selections="4")
    assert second.status_code == 200
    assert len(colab["gemini_calls"]) == 1                        # converted once
    f5_payload = colab["colab_calls"][1]["payload"]
    assert f5_payload["engine"] == "f5" and f5_payload["text"] == first["converted_text"]
    assert colab["colab_calls"][1]["timeout"] == 120
    assert second.json()["text_mode"] == "translate"


def test_converted_text_in_the_wrong_script_is_not_trusted(colab):
    colab["gemini_reply"] = "मुझे पानी चाहिए"
    r = speak(text="I need water", text_mode="translate", converted_text="I need water")
    assert r.status_code == 200
    assert len(colab["gemini_calls"]) == 1                        # converted properly instead


def test_accent_of_indian_language_speech_is_not_reported(colab):
    colab["gemini_reply"] = "मुझे पानी चाहिए"
    metrics = speak(text="I need water", text_mode="translate").json()["metrics"]
    assert metrics["output"]["accent_scores"] is None             # English accent model: meaningless here
    assert metrics["accent_match"] is None
    assert metrics["similarity"] == 0.78                          # voice similarity still shown
    assert log_rows()[-1]["out_accent"] == "" and log_rows()[-1]["accent_match"] == ""


def test_log_has_language_mode_and_both_texts_for_indian_languages_only(colab):
    colab["gemini_reply"] = "मुझे पानी चाहिए"
    speak(text="I need water", text_mode="translate")
    row = log_rows()[-1]
    assert (row["engine"], row["language"], row["text_mode"]) == ("indicf5", "hi", "translate")
    assert row["original_text"] == "I need water"
    assert row["converted_text"] == "मुझे पानी चाहिए।"

    client.post("/speak", data={"text": "I need water", "emotion": "neutral", "engine": "f5"})
    english = log_rows()[-1]
    assert english["language"] == "en"
    assert english["original_text"] == english["converted_text"] == english["text_mode"] == ""


@pytest.mark.parametrize("fields, message", [
    ({"language": "en"}, "choose one"),                            # IndicF5 needs an Indian language
    ({"engine": "xtts"}, "work with IndicF5"),                     # XTTS isn't used for these here
    ({"language": "fr"}, "Unknown language"),
])
def test_bad_combinations_are_refused_before_colab(colab, fields, message):
    r = speak(**fields)
    assert r.status_code == 400 and message in r.json()["error"]
    assert colab["colab_calls"] == []


def test_stock_voice_cannot_use_indicf5(colab, store):
    stock = store.create("Stocky", "stock", stock_speaker="Ana Florence")
    r = speak(text="मुझे पानी चाहिए", profile_id=stock["id"])
    assert r.status_code == 400 and "IndicF5 need a recorded voice" in r.json()["error"]


def test_old_requests_without_the_new_fields_send_exactly_the_same_payload(colab):
    r = client.post("/speak", data={"text": "I NEED HELP", "emotion": "neutral", "engine": "f5"})
    assert r.status_code == 200
    p = colab["colab_calls"][0]["payload"]
    assert set(p) == {"text", "language", "emotion", "engine", "stock_speaker",
                      "speaker_wav_base64", "speaker_wav_base64_list"}
    assert p["language"] == "en" and p["text"] == "I need help."
    assert "converted_text" not in r.json()
    assert r.json()["metrics"]["output"]["accent_scores"] == [["indian", 0.3], ["us", 0.1]]   # untouched


def test_language_list_endpoint(colab):
    data = client.get("/indic/languages").json()
    assert data["gemini"] is True and data["modes"] == ["transliterate", "translate"]
    assert {"code": "ta", "name": "Tamil", "script": "Tamil"} in data["languages"]


def test_indic_endpoint_is_not_given_the_no_cache_header():
    assert "Cache-Control" not in client.get("/indic/languages").headers
