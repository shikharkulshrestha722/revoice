# ============================================================
# ReVoice — User profiles + voice bank saved on disk
# ============================================================
# Each user (patient, or a demo volunteer) gets a folder:
#
#   data/profiles/<profile_id>/
#       profile.json          name, voice type, consent, date created
#       clips/<emotion>/      their recorded voice clips (newest 3 kept)
#
# The currently selected user is remembered in data/settings.json,
# so everything survives a backend restart.
#
# Deleting a profile removes its whole folder: recordings included.
# ============================================================

import json
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

EMOTIONS = ("neutral", "happy", "urgent", "tender")
VOICE_TYPES = ("cloned", "stock")
MAX_CLIPS_PER_EMOTION = 3

# Profile ids end up in folder paths, so only allow safe characters.
# This stops tricks like "../../windows" reaching other folders.
SAFE_ID = re.compile(r"^[a-z0-9-]{1,64}$")
AUDIO_EXTENSIONS = (".webm", ".wav", ".ogg", ".mp3", ".m4a")


class ProfileError(ValueError):
    """A problem the user can fix (bad name, missing consent...). Shown as a 400 error."""


class ProfileNotFound(LookupError):
    """No profile with that id. Shown as a 404 error."""


def make_profile_id(name: str) -> str:
    """'Mr Sharma' -> 'mr-sharma-3fa2' (readable, plus a random part so ids never clash)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "user"
    return f"{slug}-{secrets.token_hex(2)}"


class ProfileStore:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.profiles_dir = self.data_dir / "profiles"
        self.settings_file = self.data_dir / "settings.json"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    # ---------- helpers ----------
    def _folder(self, profile_id: str) -> Path:
        if not isinstance(profile_id, str) or not SAFE_ID.match(profile_id):
            raise ProfileNotFound(f"No profile '{profile_id}'.")
        folder = self.profiles_dir / profile_id
        if not (folder / "profile.json").is_file():
            raise ProfileNotFound(f"No profile '{profile_id}'.")
        return folder

    def _clip_folder(self, profile_id: str, emotion: str) -> Path:
        if emotion not in EMOTIONS:
            raise ProfileError(f"Unknown emotion '{emotion}'. Use one of: {', '.join(EMOTIONS)}.")
        return self._folder(profile_id) / "clips" / emotion

    def _read_settings(self) -> dict:
        try:
            return json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _write_settings(self, settings: dict):
        self.settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    # ---------- profiles ----------
    def create(self, name: str, voice_type: str = "cloned", stock_speaker: str = "",
               consent_given: bool = False) -> dict:
        name = " ".join((name or "").split())
        if not name:
            raise ProfileError("Please enter a name.")
        if len(name) > 60:
            raise ProfileError("Name is too long (max 60 characters).")
        if voice_type not in VOICE_TYPES:
            raise ProfileError("Voice type must be 'cloned' or 'stock'.")
        if voice_type == "cloned" and not consent_given:
            # we are copying a real person's voice: they must agree first
            raise ProfileError("Consent is required before cloning someone's voice.")
        if voice_type == "stock" and not stock_speaker:
            raise ProfileError("Please choose a stock voice.")

        profile_id = make_profile_id(name)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        profile = {
            "id": profile_id,
            "name": name,
            "voice_type": voice_type,
            "stock_speaker": stock_speaker if voice_type == "stock" else "",
            "consent_given": bool(consent_given),
            "consent_at": now if consent_given else None,
            "created_at": now,
        }
        folder = self.profiles_dir / profile_id
        folder.mkdir(parents=True)
        (folder / "profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
        return profile

    def get(self, profile_id: str) -> dict:
        folder = self._folder(profile_id)
        profile = json.loads((folder / "profile.json").read_text(encoding="utf-8"))
        profile["clip_counts"] = self.clip_counts(profile_id)
        return profile

    def list(self) -> list:
        """All profiles, oldest first."""
        out = []
        for folder in self.profiles_dir.iterdir():
            if (folder / "profile.json").is_file():
                try:
                    out.append(self.get(folder.name))
                except (ProfileNotFound, ValueError):
                    continue   # skip a broken folder instead of crashing the page
        return sorted(out, key=lambda p: p["created_at"])

    def delete(self, profile_id: str):
        """Removes the profile AND all its recordings from disk."""
        folder = self._folder(profile_id)
        shutil.rmtree(folder)
        if self.active_id() is None:
            # the deleted user was selected: fall back to the oldest remaining user
            remaining = self.list()
            self._write_settings({"active_profile": remaining[0]["id"] if remaining else None})

    # ---------- which user is selected ----------
    def active_id(self):
        """Id of the selected profile, or None if there isn't one (or it was deleted)."""
        pid = self._read_settings().get("active_profile")
        try:
            self._folder(pid)
            return pid
        except ProfileNotFound:
            return None

    def select(self, profile_id: str) -> dict:
        profile = self.get(profile_id)          # raises if it doesn't exist
        self._write_settings({"active_profile": profile_id})
        return profile

    # ---------- voice clips ----------
    def add_clip(self, profile_id: str, emotion: str, audio: bytes, extension: str = ".webm") -> int:
        """Saves a recording, keeps only the newest MAX_CLIPS_PER_EMOTION. Returns the clip count."""
        if self.get(profile_id)["voice_type"] != "cloned":
            raise ProfileError("This user has a stock voice, so recordings aren't used.")
        if not audio:
            raise ProfileError("The recording was empty.")
        extension = extension.lower() if extension.lower() in AUDIO_EXTENSIONS else ".webm"

        folder = self._clip_folder(profile_id, emotion)
        folder.mkdir(parents=True, exist_ok=True)
        # Files are numbered 000001, 000002, ... so sorting by name = oldest first.
        # (A counter, not the clock: Windows' clock is too coarse and two quick
        # uploads could get the same name and overwrite each other.)
        clips = self._clip_files(folder)
        next_number = max((int(f.stem) for f in clips if f.stem.isdigit()), default=0) + 1
        (folder / f"{next_number:06d}{extension}").write_bytes(audio)

        clips = self._clip_files(folder)
        for old in clips[:-MAX_CLIPS_PER_EMOTION]:
            old.unlink()
        return min(len(clips), MAX_CLIPS_PER_EMOTION)

    def _clip_files(self, folder: Path) -> list:
        if not folder.is_dir():
            return []
        return sorted(f for f in folder.iterdir() if f.suffix.lower() in AUDIO_EXTENSIONS)

    def get_clips(self, profile_id: str, emotion: str) -> list:
        """The saved recordings for one emotion, as raw bytes (oldest first)."""
        return [f.read_bytes() for f in self._clip_files(self._clip_folder(profile_id, emotion))]

    def clip_counts(self, profile_id: str) -> dict:
        clips_root = self._folder(profile_id) / "clips"
        return {e: len(self._clip_files(clips_root / e))
                for e in EMOTIONS if self._clip_files(clips_root / e)}
