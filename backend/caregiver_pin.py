"""Caregiver PIN: protects a user's voice recordings (a cloned voice can be misused).

- The PIN (4-6 digits) is never stored: only a salted PBKDF2-SHA256 hash in profile.json.
- A correct PIN gives a random session token (kept in memory only, so a server restart locks
  everything). The page sends it in the X-ReVoice-Token header. It expires after
  TOKEN_IDLE_S without use (each use restarts the 15 minutes), or at once with "Lock now".
- 5 wrong PINs in a row lock that user for 5 minutes.
- The audit log records who did what and whether it was allowed, never a PIN or any audio.
Speaking (/speak), predictions and scanning never need the PIN, so the patient can always talk.
"""

import csv
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone

PIN_PATTERN = re.compile(r"^[0-9]{4,6}$")     # plain 0-9 only (\d would also accept e.g. ١٢٣٤)
ITERATIONS = 200_000           # PBKDF2 rounds: slow for an attacker, ~0.1 s for us
TOKEN_IDLE_S = 15 * 60
MAX_ATTEMPTS = 5
LOCKOUT_S = 5 * 60
TOKEN_HEADER = "X-ReVoice-Token"


class AccessDenied(Exception):
    """A protected action was refused. code: locked | pin_not_set | wrong_pin | locked_out | bad_pin."""

    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def body(self):
        return {"error": self.message, "code": self.code, **self.extra}


def valid_pin(pin) -> bool:
    return isinstance(pin, str) and bool(PIN_PATTERN.match(pin))


def check_new_pin(pin):
    if not valid_pin(pin):
        raise AccessDenied(400, "bad_pin", "The PIN must be 4 to 6 digits.")


def hash_pin(pin: str, salt: bytes = None, iterations: int = None) -> dict:
    """What profile.json keeps: the algorithm, rounds, a random salt and the hash (no PIN)."""
    salt = salt or secrets.token_bytes(16)
    iterations = iterations or ITERATIONS
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)
    return {"algo": "pbkdf2_sha256", "iterations": iterations, "salt": salt.hex(), "hash": digest.hex()}


def verify_pin(pin: str, record: dict) -> bool:
    if not record or not isinstance(pin, str):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(record["salt"]),
                                 int(record["iterations"]))
    return hmac.compare_digest(digest.hex(), record["hash"])     # same time for any wrong PIN


class PinGuard:
    """Wrong-PIN counting, lockouts and session tokens, all in memory."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._tokens = {}        # token -> {"profile": id, "last_used": clock()}
        self._failures = {}      # profile id -> wrong PINs in a row
        self._locked_until = {}  # profile id -> clock() when the lockout ends
        self._lock = threading.Lock()

    def locked_out_for(self, profile_id) -> int:
        """Seconds left of a lockout (0 = not locked out)."""
        left = self._locked_until.get(profile_id, 0) - self.clock()
        return max(0, int(left + 0.999))

    def check_pin(self, profile_id, record, pin):
        """Raises AccessDenied (locked_out / wrong_pin) or returns quietly if the PIN is right."""
        with self._lock:
            wait = self.locked_out_for(profile_id)
            if wait:
                raise AccessDenied(429, "locked_out", f"Too many wrong PINs. Try again in {wait} s.",
                                   retry_after=wait)
            if verify_pin(pin, record):
                self._failures.pop(profile_id, None)
                return
            failures = self._failures.get(profile_id, 0) + 1
            if failures >= MAX_ATTEMPTS:
                self._failures.pop(profile_id, None)
                self._locked_until[profile_id] = self.clock() + LOCKOUT_S
                raise AccessDenied(429, "locked_out",
                                   f"{MAX_ATTEMPTS} wrong PINs: locked for {LOCKOUT_S // 60} minutes.",
                                   retry_after=LOCKOUT_S)
            self._failures[profile_id] = failures
            left = MAX_ATTEMPTS - failures
            raise AccessDenied(401, "wrong_pin", f"Wrong PIN. {left} attempt(s) left before a "
                                                 f"{LOCKOUT_S // 60}-minute lockout.", attempts_left=left)

    def issue(self, profile_id) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = {"profile": profile_id, "last_used": self.clock()}
        return token

    def seconds_left(self, token, profile_id) -> int:
        """Seconds before the token expires (0 = no valid token for this user). Doesn't refresh it."""
        entry = self._tokens.get(token or "")
        if not entry or entry["profile"] != profile_id:
            return 0
        return max(0, int(entry["last_used"] + TOKEN_IDLE_S - self.clock()))

    def use(self, token, profile_id) -> bool:
        """True if the token is valid for this user; using it restarts its 15 minutes."""
        with self._lock:
            if not self.seconds_left(token, profile_id):
                self._tokens.pop(token or "", None)              # expired: forget it
                return False
            self._tokens[token]["last_used"] = self.clock()
            return True

    def lock_profile(self, profile_id):
        """Ends every session of this user ("Lock now", PIN changed, user deleted)."""
        with self._lock:
            for token in [t for t, e in self._tokens.items() if e["profile"] == profile_id]:
                del self._tokens[token]

    def forget_profile(self, profile_id):
        self.lock_profile(profile_id)
        with self._lock:
            self._failures.pop(profile_id, None)
            self._locked_until.pop(profile_id, None)


# Audit log: data/audit_log.csv

AUDIT_FIELDS = ["timestamp", "profile_id", "action", "allowed", "detail"]
_audit_lock = threading.Lock()


def audit(path, profile_id, action, allowed, detail=""):
    """One row: when, which user, what, allowed or not, and why not. Never a PIN or audio."""
    row = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "profile_id": profile_id or "", "action": action, "allowed": "yes" if allowed else "no",
           "detail": (detail or "")[:120]}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with _audit_lock:
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=AUDIT_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
    return row


def read_audit(path, profile_id, limit=200):
    """This user's rows, newest first."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["profile_id"] == profile_id]
    return rows[::-1][:limit]
