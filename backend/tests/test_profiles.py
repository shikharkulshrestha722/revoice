# Profiles and the on-disk voice bank (profiles.py and /profiles endpoints).
import json

import pytest
import requests
from fastapi.testclient import TestClient

import app as backend
from profiles import ProfileError, ProfileNotFound, ProfileStore

client = TestClient(backend.app)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data
        self.text = ""

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def upload(emotion="neutral", data=b"audio", profile_id=""):
    return client.post("/upload_voice_clip",
                       data={"emotion": emotion, "profile_id": profile_id},
                       files={"file": ("clip.webm", data, "audio/webm")})


# ProfileStore (the disk storage)

def test_create_writes_profile_json(store):
    p = store.create("Mr  Sharma", "cloned", consent_given=True)
    saved = json.loads((store.profiles_dir / p["id"] / "profile.json").read_text())
    assert saved["name"] == "Mr Sharma"             # extra spaces tidied
    assert saved["voice_type"] == "cloned"
    assert saved["consent_given"] is True
    assert saved["consent_at"] and saved["created_at"]
    assert p["id"].startswith("mr-sharma-")


def test_cloned_voice_requires_consent(store):
    with pytest.raises(ProfileError, match="Consent"):
        store.create("Teacher", "cloned", consent_given=False)


def test_stock_voice_needs_no_consent_but_needs_a_speaker(store):
    p = store.create("Patient", "stock", stock_speaker="Ana Florence")
    assert p["stock_speaker"] == "Ana Florence"
    with pytest.raises(ProfileError, match="stock voice"):
        store.create("Patient", "stock", stock_speaker="")


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
def test_bad_names_are_refused(store, name):
    with pytest.raises(ProfileError):
        store.create(name, "stock", stock_speaker="Ana Florence")


def test_unknown_voice_type_is_refused(store):
    with pytest.raises(ProfileError):
        store.create("A", "robot")


def test_data_survives_a_restart(store):
    """A new ProfileStore on the same folder = the backend restarting."""
    p = store.create("Asha", "cloned", consent_given=True)
    store.select(p["id"])
    store.add_clip(p["id"], "happy", b"hello")

    restarted = ProfileStore(store.data_dir)
    assert restarted.active_id() == p["id"]
    assert restarted.get_clips(p["id"], "happy") == [b"hello"]
    assert [x["id"] for x in restarted.list()] == [p["id"]]


def test_keeps_newest_three_clips_per_emotion(store):
    p = store.create("Asha", "cloned", consent_given=True)
    for i in range(5):
        count = store.add_clip(p["id"], "urgent", f"c{i}".encode())
    assert count == 3
    assert store.get_clips(p["id"], "urgent") == [b"c2", b"c3", b"c4"]
    assert store.clip_counts(p["id"]) == {"urgent": 3}


def test_stock_profile_cannot_store_clips(store):
    p = store.create("Patient", "stock", stock_speaker="Ana Florence")
    with pytest.raises(ProfileError):
        store.add_clip(p["id"], "neutral", b"audio")


def test_unsafe_ids_are_rejected(store):
    for bad in ["../settings", "..\\x", "A-UPPER", "", None, "x" * 65]:
        with pytest.raises(ProfileNotFound):
            store.get(bad)


def test_delete_removes_folder_and_recordings(store):
    p = store.create("Teacher", "cloned", consent_given=True)
    store.add_clip(p["id"], "neutral", b"teacher voice")
    folder = store.profiles_dir / p["id"]

    store.delete(p["id"])

    assert not folder.exists()
    with pytest.raises(ProfileNotFound):
        store.get(p["id"])


def test_deleting_selected_user_falls_back_to_oldest_remaining(store):
    first = store.create("Patient", "stock", stock_speaker="Ana Florence")
    demo = store.create("Teacher", "cloned", consent_given=True)
    store.select(demo["id"])

    store.delete(demo["id"])
    assert store.active_id() == first["id"]

    store.delete(first["id"])
    assert store.active_id() is None


# /profiles endpoints

def test_no_profiles_at_start():
    assert client.get("/profiles").json() == {"profiles": [], "active": None}
    assert client.get("/voice_bank").json() == {}


def test_create_selects_the_new_user():
    r = client.post("/profiles", data={"name": "Asha", "voice_type": "cloned", "consent_given": "true"})
    assert r.status_code == 200
    new_id = r.json()["id"]
    listing = client.get("/profiles").json()
    assert listing["active"] == new_id
    assert [p["name"] for p in listing["profiles"]] == ["Asha"]


def test_create_cloned_without_consent_is_400():
    r = client.post("/profiles", data={"name": "Teacher", "voice_type": "cloned"})
    assert r.status_code == 400
    assert "Consent" in r.json()["error"]


def test_select_switches_user_and_voice_bank():
    a = client.post("/profiles", data={"name": "A", "consent_given": "true"}).json()
    b = client.post("/profiles", data={"name": "B", "consent_given": "true"}).json()
    upload("happy")                                   # goes to B (selected)

    client.post(f"/profiles/{a['id']}/select")
    assert client.get("/profiles").json()["active"] == a["id"]
    assert client.get("/voice_bank").json() == {}
    assert client.get(f"/voice_bank?profile_id={b['id']}").json() == {"happy": 1}


def test_select_unknown_user_is_404():
    assert client.post("/profiles/nobody-0000/select").status_code == 404


def test_upload_without_any_user_is_400():
    r = upload()
    assert r.status_code == 400
    assert "Add user" in r.json()["error"]


def test_delete_endpoint_removes_user_and_tells_colab(store, monkeypatch):
    calls = []
    monkeypatch.setattr(backend.requests, "post",
                        lambda url, **kw: calls.append(url) or FakeResponse(200, {}))
    monkeypatch.setattr(backend, "COLAB_API_URL", "https://colab.test")
    t = client.post("/profiles", data={"name": "Teacher", "consent_given": "true"}).json()
    upload("neutral", b"teacher")

    r = client.delete(f"/profiles/{t['id']}")

    assert r.json() == {"deleted": t["id"], "active": None, "colab_cache_cleared": True}
    assert calls == ["https://colab.test/forget_voices"]
    assert not (store.profiles_dir / t["id"]).exists()


def test_delete_still_works_when_colab_is_offline(store, monkeypatch):
    def offline(*a, **kw):
        raise requests.exceptions.ConnectionError("offline")
    monkeypatch.setattr(backend.requests, "post", offline)
    t = client.post("/profiles", data={"name": "Teacher", "consent_given": "true"}).json()

    r = client.delete(f"/profiles/{t['id']}")
    assert r.status_code == 200
    assert r.json()["colab_cache_cleared"] is False
    assert store.list() == []


def test_delete_unknown_user_is_404():
    assert client.delete("/profiles/nobody-0000").status_code == 404


# Stock voices

def test_stock_speakers_from_colab(monkeypatch):
    monkeypatch.setattr(backend.requests, "get",
                        lambda url, **kw: FakeResponse(200, {"speakers": ["Ana Florence", "Viktor Eka"]}))
    assert client.get("/stock_speakers").json() == {
        "speakers": ["Ana Florence", "Viktor Eka"], "source": "colab"}


def test_stock_speakers_fallback_when_colab_offline(monkeypatch):
    def offline(*a, **kw):
        raise requests.exceptions.ConnectionError("offline")
    monkeypatch.setattr(backend.requests, "get", offline)
    r = client.get("/stock_speakers").json()
    assert r["source"] == "fallback"
    assert "Ana Florence" in r["speakers"]


def test_speak_with_stock_voice_sends_speaker_name(monkeypatch):
    sent = {}
    monkeypatch.setattr(backend.requests, "post",
                        lambda url, json=None, **kw: sent.update(json) or FakeResponse(200, {"audio_base64": "x"}))
    client.post("/profiles", data={"name": "Patient", "voice_type": "stock", "stock_speaker": "Ana Florence"})

    r = client.post("/speak", data={"text": "I AM COLD", "emotion": "neutral"})

    assert r.status_code == 200
    assert sent["stock_speaker"] == "Ana Florence"
    assert sent["text"] == "I am cold."
    assert "speaker_wav_base64_list" not in sent      # no recordings involved


def test_speak_stock_voice_with_f5_is_refused():
    client.post("/profiles", data={"name": "Patient", "voice_type": "stock", "stock_speaker": "Ana Florence"})
    r = client.post("/speak", data={"text": "HI", "emotion": "neutral", "engine": "f5"})
    assert r.status_code == 400
    assert "XTTS" in r.json()["error"]


def test_speak_can_target_a_profile_that_is_not_selected(monkeypatch):
    sent = {}
    monkeypatch.setattr(backend.requests, "post",
                        lambda url, json=None, **kw: sent.update(json) or FakeResponse(200, {"audio_base64": "x"}))
    stock = client.post("/profiles", data={"name": "P", "voice_type": "stock",
                                           "stock_speaker": "Viktor Eka"}).json()
    client.post("/profiles", data={"name": "Other", "consent_given": "true"})   # now selected

    client.post("/speak", data={"text": "HI", "emotion": "neutral", "profile_id": stock["id"]})
    assert sent["stock_speaker"] == "Viktor Eka"
