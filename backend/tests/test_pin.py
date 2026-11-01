# Caregiver PIN: hashing, lockout, session tokens, every protected endpoint, the audit log,
# and that speaking never needs the PIN. Colab and Gemini are mocked.
import csv
import inspect
import json
import re

import pytest
from fastapi.testclient import TestClient

import app as backend
import caregiver_pin
from caregiver_pin import PinGuard, hash_pin, valid_pin, verify_pin
from tests.test_api import FakeResponse
from tests.test_switch import FakeClock

client = TestClient(backend.app)
PIN = "4826"
H = caregiver_pin.TOKEN_HEADER
REAL_ITERATIONS = caregiver_pin.ITERATIONS        # read before conftest lowers it for each test


@pytest.fixture
def clock(monkeypatch, pin_on):
    """The real PIN check with a clock the test controls."""
    fake = FakeClock()
    guard = PinGuard(clock=fake)
    monkeypatch.setattr(backend, "guard", guard)
    return fake


@pytest.fixture(autouse=True)
def colab_forgets(monkeypatch):
    """Deleting a user tells Colab to forget cached voices: a fake Colab answers."""
    monkeypatch.setattr(backend, "COLAB_API_URL", "https://colab.test")
    monkeypatch.setattr(backend.requests, "post", lambda *a, **k: FakeResponse(200, {"forgotten": 0}))


def new_user(name="Asha", pin=PIN, **extra):
    data = {"name": name, "voice_type": "cloned", "consent_given": "true", **extra}
    if pin is not None:
        data["pin"] = pin
    r = client.post("/profiles", data=data)
    assert r.status_code == 200, r.text
    return r.json()


def unlock(pid, pin=PIN):
    return client.post(f"/profiles/{pid}/unlock", data={"pin": pin})


def auth(token):
    return {H: token}


# Hashing

@pytest.mark.parametrize("pin, ok", [("1234", True), ("123456", True), ("0000", True), ("123", False),
                                     ("1234567", False), ("12a4", False), (" 1234", False), ("", False),
                                     ("١٢٣٤", False), (1234, False)])
def test_pin_must_be_4_to_6_plain_digits(pin, ok):
    assert valid_pin(pin) is ok


def test_hash_is_salted_and_verifiable_and_holds_no_pin():
    a, b = hash_pin(PIN), hash_pin(PIN)
    assert a["hash"] != b["hash"] and a["salt"] != b["salt"]          # random salt each time
    assert a["algo"] == "pbkdf2_sha256" and a["iterations"] == caregiver_pin.ITERATIONS
    assert PIN not in json.dumps(a)
    assert verify_pin(PIN, a) and not verify_pin("4827", a) and not verify_pin(PIN, None)


def test_real_strength_is_200000_rounds():
    # the tests run with fewer rounds for speed (conftest); the app's own setting:
    assert REAL_ITERATIONS == 200_000


# Creating, unlocking, lockout

def test_new_user_with_pin_stores_only_a_hash_and_gets_a_token(pin_on, store):
    user = new_user()
    assert user["pin_set"] is True and "pin" not in user and user["token"]
    saved = json.loads((store.profiles_dir / user["id"] / "profile.json").read_text(encoding="utf-8"))
    assert "hash" in saved["pin"] and PIN not in json.dumps(saved)
    listed = client.get("/profiles").json()["profiles"][0]
    assert "pin" not in listed and listed["pin_set"] is True


def test_bad_pin_on_create_is_refused_before_anything_is_created(pin_on, store):
    r = client.post("/profiles", data={"name": "Asha", "voice_type": "cloned", "consent_given": "true", "pin": "12"})
    assert r.status_code == 400 and r.json()["code"] == "bad_pin"
    assert store.list() == []


def test_unlock_wrong_pin_counts_down_then_locks_out_for_5_minutes(clock):
    pid = new_user()["id"]
    for left in (4, 3, 2, 1):
        r = unlock(pid, "0000")
        assert r.status_code == 401 and r.json() == {**r.json(), "code": "wrong_pin", "attempts_left": left}
    r = unlock(pid, "0000")
    assert r.status_code == 429 and r.json()["code"] == "locked_out" and r.json()["retry_after"] == 300
    assert unlock(pid).status_code == 429                     # even the right PIN, during the lockout
    clock.now += 299
    assert unlock(pid).status_code == 429
    clock.now += 2
    assert unlock(pid).status_code == 200


def test_a_correct_pin_resets_the_wrong_count(clock):
    pid = new_user()["id"]
    for _ in range(4):
        unlock(pid, "0000")
    assert unlock(pid).status_code == 200
    assert unlock(pid, "0000").json()["attempts_left"] == 4


def test_lockout_is_per_user(clock):
    a, b = new_user("Asha")["id"], new_user("Ravi")["id"]
    for _ in range(5):
        unlock(a, "0000")
    assert unlock(a).status_code == 429
    assert unlock(b).status_code == 200


# Session tokens

def test_token_expires_after_15_minutes_without_use_and_each_use_restarts_it(clock):
    user = new_user()
    pid, token = user["id"], user["token"]
    url = f"/uploads?profile_id={pid}"
    clock.now += 14 * 60
    assert client.get(url, headers=auth(token)).status_code == 200     # used: 15 more minutes
    clock.now += 14 * 60
    assert client.get(url, headers=auth(token)).status_code == 200
    clock.now += 15 * 60 + 1
    r = client.get(url, headers=auth(token))
    assert r.status_code == 401 and r.json()["code"] == "locked"


def test_lock_now_ends_the_session_at_once(pin_on):
    user = new_user()
    assert client.post(f"/profiles/{user['id']}/lock").json() == {"locked": True}
    assert client.get(f"/uploads?profile_id={user['id']}", headers=auth(user["token"])).status_code == 401


def test_a_token_only_opens_its_own_user(pin_on):
    a, b = new_user("Asha"), new_user("Ravi")
    assert client.get(f"/uploads?profile_id={b['id']}", headers=auth(a["token"])).status_code == 401
    assert client.get(f"/uploads?profile_id={b['id']}", headers=auth(b["token"])).status_code == 200


def test_lock_status_for_the_indicator(clock):
    user = new_user()
    status = client.get(f"/profiles/{user['id']}/lock_status", headers=auth(user["token"])).json()
    assert status == {"pin_set": True, "seconds_left": 900, "locked_out_seconds": 0}
    clock.now += 60
    assert client.get(f"/profiles/{user['id']}/lock_status", headers=auth(user["token"])).json()["seconds_left"] == 840
    assert client.get(f"/profiles/{user['id']}/lock_status").json()["seconds_left"] == 0


# Setting and changing the PIN

def test_existing_user_without_pin_is_asked_to_set_one_then_can_continue(pin_on):
    pid = new_user(pin=None)["id"]
    r = client.get(f"/uploads?profile_id={pid}")
    assert r.status_code == 403 and r.json()["code"] == "pin_not_set"
    assert unlock(pid).json()["code"] == "pin_not_set"
    token = client.post(f"/profiles/{pid}/pin", data={"new_pin": "5555"}).json()["token"]
    assert client.get(f"/uploads?profile_id={pid}", headers=auth(token)).status_code == 200


def test_changing_the_pin_needs_the_old_one_and_ends_old_sessions(pin_on):
    user = new_user()
    pid = user["id"]
    r = client.post(f"/profiles/{pid}/pin", data={"old_pin": "0000", "new_pin": "777777"})
    assert r.status_code == 401 and r.json()["attempts_left"] == 4       # counts towards the lockout
    assert client.post(f"/profiles/{pid}/pin", data={"new_pin": "777777"}).status_code == 401
    r = client.post(f"/profiles/{pid}/pin", data={"old_pin": PIN, "new_pin": "777777"})
    assert r.status_code == 200 and r.json()["token"]
    assert client.get(f"/uploads?profile_id={pid}", headers=auth(user["token"])).status_code == 401
    assert unlock(pid).status_code == 401 and unlock(pid, "777777").status_code == 200


def test_new_pin_must_be_valid(pin_on):
    pid = new_user()["id"]
    r = client.post(f"/profiles/{pid}/pin", data={"old_pin": PIN, "new_pin": "12ab"})
    assert r.status_code == 400 and r.json()["code"] == "bad_pin"


# Every protected endpoint

def protected_requests(pid):
    """(method, url, form data, files) for every endpoint that needs the PIN."""
    wav = ("clip.wav", b"RIFF....", "audio/wav")
    return [
        ("get", f"/uploads?profile_id={pid}", None, None),
        ("post", "/uploads", {"profile_id": pid, "start_s": "0", "end_s": "1"}, {"file": wav}),
        ("delete", f"/uploads/000001?profile_id={pid}", None, None),
        ("post", "/upload_voice_clip", {"profile_id": pid, "emotion": "neutral"}, {"file": ("c.webm", b"x", "audio/webm")}),
        ("get", f"/guided/status?profile_id={pid}", None, None),
        ("post", "/guided/record", {"profile_id": pid, "sentence_id": "g001", "duration_s": "2"}, {"file": ("c.webm", b"x", "audio/webm")}),
        ("post", "/guided/skip", {"profile_id": pid, "sentence_id": "g001"}, None),
        ("get", f"/guided/audio/g001?profile_id={pid}", None, None),
        ("post", "/guided/references", {"profile_id": pid}, None),
        ("delete", f"/guided/references?profile_id={pid}", None, None),
        ("get", f"/profiles/{pid}/export", None, None),
        ("get", f"/profiles/{pid}/audit", None, None),
        ("delete", f"/profiles/{pid}", None, None),
    ]


def send(method, url, data, files, headers=None):
    if method == "get":
        return client.get(url, headers=headers)
    if method == "delete":
        return client.delete(url, headers=headers)
    return client.post(url, data=data, files=files, headers=headers)


@pytest.mark.parametrize("index", range(13))
def test_every_protected_endpoint_refuses_without_a_valid_token(pin_on, index, store):
    pid = new_user()["id"]
    method, url, data, files = protected_requests(pid)[index]
    for headers in (None, auth("made-up-token")):
        r = send(method, url, data, files, headers)
        assert r.status_code == 401 and r.json()["code"] == "locked", url
    assert store.get(pid)["pin_set"]                               # nothing was deleted


@pytest.mark.parametrize("index", range(13))
def test_every_protected_endpoint_asks_for_a_pin_on_users_without_one(pin_on, index):
    pid = new_user(pin=None)["id"]
    method, url, data, files = protected_requests(pid)[index]
    r = send(method, url, data, files)
    assert r.status_code == 403 and r.json()["code"] == "pin_not_set", url


def test_no_endpoint_handles_recordings_without_the_pin_check():
    """Guards against a new endpoint forgetting require_unlocked: the protected list is exact."""
    checked = set()
    for route in backend.app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint and "require_unlocked(" in inspect.getsource(endpoint):
            for method in route.methods:
                checked.add((method, route.path))
    assert checked == {
        ("GET", "/uploads"), ("POST", "/uploads"), ("DELETE", "/uploads/{clip_id}"),
        ("POST", "/upload_voice_clip"), ("GET", "/guided/status"), ("POST", "/guided/record"),
        ("POST", "/guided/skip"), ("GET", "/guided/audio/{sentence_id}"), ("POST", "/guided/references"),
        ("DELETE", "/guided/references"), ("GET", "/profiles/{profile_id}/export"),
        ("GET", "/profiles/{profile_id}/audit"), ("DELETE", "/profiles/{profile_id}"),
    }


def test_with_the_token_recordings_can_be_managed_and_the_user_deleted(pin_on, store):
    user = new_user()
    pid, h = user["id"], auth(user["token"])
    r = client.post("/upload_voice_clip", data={"profile_id": pid, "emotion": "neutral"},
                    files={"file": ("c.webm", b"voice", "audio/webm")}, headers=h)
    assert r.status_code == 200 and r.json()["clips"] == 1
    assert client.get(f"/guided/status?profile_id={pid}", headers=h).status_code == 200
    assert client.delete(f"/profiles/{pid}", headers=h).status_code == 200
    assert store.list() == []


# The patient can always talk

def test_speaking_predicting_and_scanning_never_need_the_pin(pin_on, monkeypatch, store):
    pid = new_user()["id"]
    client.post(f"/profiles/{pid}/lock")
    store.add_clip(pid, "neutral", b"voice")                       # recorded earlier
    monkeypatch.setattr(backend, "COLAB_API_URL", "https://colab.test")
    monkeypatch.setattr(backend.requests, "post", lambda *a, **k: FakeResponse(200, {"audio_base64": "UklGRg=="}))
    monkeypatch.setattr(backend, "GEMINI_API_KEY", "")
    assert client.post("/speak", data={"text": "I need water", "emotion": "neutral", "engine": "f5"}).status_code == 200
    assert client.post("/predict", data={"text": "I NEED"}).status_code == 200
    assert client.post("/switch/simulate", json={"state": "DOWN"}).status_code == 200
    assert client.post(f"/profiles/{pid}/select").status_code == 200
    assert client.get("/voice_bank").json() == {"neutral": 1}
    assert client.get("/profiles").status_code == 200


# Audit log

def test_audit_log_records_attempts_without_the_pin_and_is_behind_the_pin(pin_on):
    user = new_user()
    pid = user["id"]
    unlock(pid, "9999")                                            # wrong
    client.get(f"/uploads?profile_id={pid}")                       # no token
    token = unlock(pid).json()["token"]
    client.get(f"/uploads?profile_id={pid}", headers=auth(token))
    client.post(f"/profiles/{pid}/lock")

    assert client.get(f"/profiles/{pid}/audit").status_code == 401
    token = unlock(pid).json()["token"]
    rows = client.get(f"/profiles/{pid}/audit", headers=auth(token)).json()["rows"]
    seen = [(r["action"], r["allowed"], r["detail"]) for r in rows[::-1]]    # oldest first
    assert seen[:6] == [("set_pin", "yes", "new user"), ("unlock", "no", "wrong_pin"),
                        ("list_uploads", "no", "locked"), ("unlock", "yes", ""),
                        ("list_uploads", "yes", ""), ("lock", "yes", "")]
    raw = open(backend.AUDIT_LOG, encoding="utf-8").read()
    assert PIN not in raw and "9999" not in raw and user["token"] not in raw and token not in raw
    with open(backend.AUDIT_LOG, newline="", encoding="utf-8") as f:
        assert csv.DictReader(f).fieldnames == ["timestamp", "profile_id", "action", "allowed", "detail"]


def test_audit_log_shows_only_that_users_rows(pin_on):
    a, b = new_user("Asha"), new_user("Ravi")
    unlock(b["id"], "0000")
    rows = client.get(f"/profiles/{a['id']}/audit", headers=auth(a["token"])).json()["rows"]
    assert {r["profile_id"] for r in rows} == {a["id"]}


def test_deleting_a_user_forgets_its_sessions_and_lockout(clock):
    user = new_user()
    pid = user["id"]
    for _ in range(4):
        unlock(pid, "0000")
    assert client.delete(f"/profiles/{pid}", headers=auth(user["token"])).status_code == 200
    assert backend.guard.seconds_left(user["token"], pid) == 0
    assert pid not in backend.guard._failures


def test_unlock_and_pin_endpoints_run_on_a_worker_thread():
    """PBKDF2 takes ~0.1 s: these must be plain def, not async def (see test_no_freeze)."""
    for fn in (backend.unlock_profile, backend.set_pin, backend.create_profile):
        assert not inspect.iscoroutinefunction(fn)
