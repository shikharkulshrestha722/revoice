# Tests for the generation log (data/generation_log.csv) and its summary.
import csv

import pytest
import requests
from fastapi.testclient import TestClient

import app as backend
from generation_log import FIELDS, letter_by_letter_selections, log_generation, saved_percent, summarize

client = TestClient(backend.app)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {"audio_base64": "x"}
        self.text = ""

    def json(self):
        return self._json


@pytest.fixture
def user(store):
    p = store.create("Asha", "cloned", consent_given=True)
    store.select(p["id"])
    store.add_clip(p["id"], "neutral", b"voice")
    return p


def log_rows():
    with open(backend.GENERATION_LOG, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def speak(**fields):
    data = {"text": "I NEED WATER ", "emotion": "neutral"}
    data.update(fields)
    return client.post("/speak", data=data)


# ================= small helpers =================

def test_letter_by_letter_counts_letters_and_single_spaces():
    assert letter_by_letter_selections("I NEED WATER") == 12        # 10 letters + 2 spaces
    assert letter_by_letter_selections("  I  NEED   WATER  ") == 12  # extra spaces ignored
    assert letter_by_letter_selections("") == 0


def test_saved_percent():
    assert saved_percent(3, 12) == 75.0
    assert saved_percent(12, 12) == 0.0
    assert saved_percent(15, 12) == -25.0       # more selections than spelling (lots of DEL)
    assert saved_percent(None, 12) is None
    assert saved_percent(3, 0) is None


# ================= /speak writes the log =================

def test_successful_speak_is_logged(user, monkeypatch):
    monkeypatch.setattr(backend.requests, "post", lambda *a, **kw: FakeResponse(200))
    r = speak(engine="f5", selections="4", utterance_id="u1")
    assert r.status_code == 200

    rows = log_rows()
    assert len(rows) == 1
    row = rows[0]
    assert list(row.keys()) == FIELDS
    assert row["engine"] == "f5" and row["emotion"] == "neutral"
    assert row["voice_source"] == "emotion_clips"
    assert row["chars"] == "12" and row["words"] == "3"
    assert row["selections"] == "4" and row["letter_by_letter"] == "12"
    assert row["selections_saved_pct"] == "66.7"
    assert row["success"] == "yes" and row["error"] == ""
    assert float(row["seconds"]) >= 0
    assert row["utterance_id"] == "u1"
    assert "WATER" not in open(backend.GENERATION_LOG, encoding="utf-8").read()   # text not stored


def test_colab_failure_is_logged_with_reason(user, monkeypatch):
    monkeypatch.setattr(backend.requests, "post",
                        lambda *a, **kw: FakeResponse(500, {"error": "CUDA out of memory"}))
    assert speak().status_code == 502
    row = log_rows()[0]
    assert row["success"] == "no"
    assert "CUDA out of memory" in row["error"]
    assert row["seconds"] != ""                  # Colab was reached, so time is known


def test_colab_unreachable_is_logged(user, monkeypatch):
    def offline(*a, **kw):
        raise requests.exceptions.ConnectionError("refused")
    monkeypatch.setattr(backend.requests, "post", offline)
    speak()
    assert log_rows()[0]["success"] == "no"
    assert "Could not reach Colab" in log_rows()[0]["error"]


def test_rejected_requests_are_logged_without_time(user):
    speak(engine="robot")                         # unknown engine
    speak(emotion="happy")                        # no clips for this emotion
    rows = log_rows()
    assert [r["success"] for r in rows] == ["no", "no"]
    assert "Unknown engine" in rows[0]["error"] and "No voice sample" in rows[1]["error"]
    assert rows[0]["seconds"] == "" and rows[1]["seconds"] == ""


def test_no_user_selected_is_logged_and_still_400(store):
    r = speak()
    assert r.status_code == 400
    assert log_rows()[0]["success"] == "no"


def test_stock_and_guided_voice_sources_are_logged(store, monkeypatch):
    monkeypatch.setattr(backend.requests, "post", lambda *a, **kw: FakeResponse(200))
    client.post("/profiles", data={"name": "P", "voice_type": "stock", "stock_speaker": "Ana Florence"})
    speak()
    g = store.create("G", "cloned", consent_given=True)
    store.select(g["id"])
    store.save_guided(g["id"], "g001", b"a", ".webm", 5.0)
    store.pick_references(g["id"])
    speak()
    assert [r["voice_source"] for r in log_rows()] == ["stock", "guided_references"]


def test_bad_selections_value_is_ignored(user, monkeypatch):
    monkeypatch.setattr(backend.requests, "post", lambda *a, **kw: FakeResponse(200))
    assert speak(selections="lots").status_code == 200
    assert log_rows()[0]["selections"] == ""


# ================= summary =================

def test_summary_per_engine_and_selections(tmp_path):
    path = str(tmp_path / "log.csv")
    log_generation(path, text="I NEED WATER", engine="xtts", emotion="neutral", seconds=4.0,
                   success=True, selections=3, utterance_id="a")
    log_generation(path, text="I NEED WATER", engine="f5", emotion="neutral", seconds=10.0,
                   success=True, selections=3, utterance_id="a")          # same sentence, compare mode
    log_generation(path, text="CALL THE NURSE", engine="xtts", emotion="urgent", seconds=6.0,
                   success=True, selections=14, utterance_id="b")         # spelled fully, no saving
    log_generation(path, text="HELLO", engine="xtts", emotion="happy", seconds=None,
                   success=False, error="Colab offline", selections=5, utterance_id="c")

    s = summarize(path)

    assert s["total_generations"] == 4 and s["succeeded"] == 3
    x = s["engines"]["xtts"]
    assert (x["requests"], x["succeeded"], x["failed"]) == (3, 2, 1)
    assert x["avg_seconds"] == 5.0 and x["min_seconds"] == 4.0 and x["max_seconds"] == 6.0
    assert x["success_rate_pct"] == 66.7
    assert s["engines"]["f5"]["avg_seconds"] == 10.0

    sel = s["selections"]
    assert sel["sentences"] == 3                    # "a" counted once
    # a: 3 of 12 -> 75%, b: 14 of 14 -> 0%, c: 5 of 5 -> 0%
    assert sel["avg_saved_pct"] == 25.0
    assert sel["overall_saved_pct"] == round(100 * (31 - 22) / 31, 1)
    assert sel["avg_selections"] == round(22 / 3, 1)


def test_summary_of_missing_log_is_empty(tmp_path):
    s = summarize(str(tmp_path / "nothing.csv"))
    assert s["total_generations"] == 0
    assert s["engines"] == {}
    assert s["selections"]["sentences"] == 0 and s["selections"]["avg_saved_pct"] is None


def test_stats_endpoint(user, monkeypatch):
    monkeypatch.setattr(backend.requests, "post", lambda *a, **kw: FakeResponse(200))
    speak(selections="2", utterance_id="z")
    s = client.get("/stats/summary").json()
    assert s["engines"]["xtts"]["succeeded"] == 1
    assert s["selections"]["sentences"] == 1
