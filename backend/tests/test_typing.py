# Typed sentences (text box) go through exactly the same /speak steps as pointer
# sentences, and are logged as typed so they never inflate "selections saved".
import base64
import csv

import pytest
from fastapi.testclient import TestClient

import app as backend
from generation_log import summarize

client = TestClient(backend.app)


class FakeOk:
    status_code = 200

    def json(self):
        return {"audio_base64": "x"}


@pytest.fixture
def colab(monkeypatch):
    sent = {}
    monkeypatch.setattr(backend.requests, "post", lambda url, json=None, **kw: sent.update(json) or FakeOk())
    return sent


@pytest.fixture
def user(store):
    p = store.create("Asha", "cloned", consent_given=True)
    store.select(p["id"])
    store.add_clip(p["id"], "happy", b"happy-clip")
    return p


def log_rows():
    with open(backend.GENERATION_LOG, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.mark.parametrize("typed_text, spoken", [
    ("Hello, Doctor Rao. Does it hurt?", "Hello, Doctor Rao. Does it hurt?"),   # typed properly: unchanged
    ("i need water", "I need water!"),                                         # same clean-up as pointer text
    ("I NEED WATER", "I need water!"),                                         # ALL CAPS, like the grid
])
def test_typed_text_goes_through_the_same_steps(user, colab, typed_text, spoken):
    r = client.post("/speak", data={"text": typed_text, "emotion": "happy", "engine": "f5",
                                    "utterance_id": "u1", "input_method": "keyboard"})
    assert r.status_code == 200
    assert colab["text"] == spoken == backend.prepare_text_for_speech(typed_text, "happy")
    assert colab["emotion"] == "happy" and colab["engine"] == "f5"
    assert [base64.b64decode(c) for c in colab["speaker_wav_base64_list"]] == [b"happy-clip"]  # same reference choice


def test_pointer_and_typed_versions_of_a_sentence_send_the_same_request(user, colab):
    client.post("/speak", data={"text": "I NEED WATER ", "emotion": "happy", "selections": "4",
                                "utterance_id": "a", "input_method": "pointer"})
    pointed = dict(colab)
    client.post("/speak", data={"text": "I NEED WATER ", "emotion": "happy", "utterance_id": "b",
                                "input_method": "keyboard"})
    assert colab == pointed


def test_typed_sentences_are_logged_but_never_count_as_savings(user, colab):
    client.post("/speak", data={"text": "I NEED WATER ", "emotion": "happy", "selections": "4",
                                "utterance_id": "pointed", "input_method": "pointer"})
    client.post("/speak", data={"text": "Please call my son now", "emotion": "happy",
                                "utterance_id": "typed", "input_method": "keyboard"})
    pointed, typed = log_rows()[-2:]
    assert pointed["input_method"] == "pointer" and pointed["selections"] == "4"
    assert typed["input_method"] == "keyboard" and typed["selections"] == "" and typed["success"] == "yes"
    sel = summarize(backend.GENERATION_LOG)["selections"]
    assert sel["sentences"] == 1 and sel["avg_saved_pct"] == 66.7     # only the pointed sentence


def test_unknown_input_method_is_not_logged(user, colab):
    client.post("/speak", data={"text": "HI", "emotion": "happy", "input_method": "<script>"})
    assert log_rows()[-1]["input_method"] == ""
