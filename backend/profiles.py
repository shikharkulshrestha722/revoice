# ============================================================
# ReVoice — User profiles + voice bank saved on disk
# ============================================================
# Each user (patient, or a demo volunteer) gets a folder:
#
#   data/profiles/<profile_id>/
#       profile.json          name, voice type, consent, date created
#       clips/<emotion>/      their recorded voice clips (newest 3 kept)
#       guided/               guided voice banking: one clip per sentence +
#                             manifest.json with the exact words of each
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

from guided_sentences import GUIDED_SENTENCES, SENTENCE_BY_ID

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

    # ---------- guided voice banking ----------
    # data/profiles/<id>/guided/
    #     g001.webm, g002.webm ...   one recording per sentence
    #     manifest.json              for each recording: the EXACT sentence read,
    #                                its length, when it was made; plus which
    #                                sentences were skipped and which recordings
    #                                are used as voice references
    def _guided_dir(self, profile_id: str) -> Path:
        return self._folder(profile_id) / "guided"

    def _read_manifest(self, profile_id: str) -> dict:
        try:
            m = json.loads((self._guided_dir(profile_id) / "manifest.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            m = {}
        m.setdefault("recordings", {})
        m.setdefault("skipped", [])
        m.setdefault("references", [])
        return m

    def _write_manifest(self, profile_id: str, manifest: dict):
        folder = self._guided_dir(profile_id)
        folder.mkdir(exist_ok=True)
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def _check_sentence(self, sentence_id: str) -> str:
        if sentence_id not in SENTENCE_BY_ID:
            raise ProfileError(f"Unknown sentence '{sentence_id}'.")
        return SENTENCE_BY_ID[sentence_id]

    def save_guided(self, profile_id: str, sentence_id: str, audio: bytes,
                    extension: str = ".webm", duration_s: float = 0.0) -> dict:
        """Saves (or replaces, for Redo) the recording of one sentence."""
        if self.get(profile_id)["voice_type"] != "cloned":
            raise ProfileError("This user has a stock voice, so recordings aren't used.")
        text = self._check_sentence(sentence_id)
        if not audio:
            raise ProfileError("The recording was empty.")
        if not (0.5 <= duration_s <= 60):
            raise ProfileError("Recording length must be between half a second and a minute.")
        extension = extension.lower() if extension.lower() in AUDIO_EXTENSIONS else ".webm"

        folder = self._guided_dir(profile_id)
        folder.mkdir(exist_ok=True)
        for old in folder.glob(f"{sentence_id}.*"):      # Redo: remove the previous take
            if old.suffix.lower() in AUDIO_EXTENSIONS:
                old.unlink()
        (folder / f"{sentence_id}{extension}").write_bytes(audio)

        manifest = self._read_manifest(profile_id)
        manifest["recordings"][sentence_id] = {
            "file": f"{sentence_id}{extension}",
            "text": text,                      # the exact words, kept with the clip
            "duration_s": round(float(duration_s), 2),
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if sentence_id in manifest["skipped"]:
            manifest["skipped"].remove(sentence_id)
        self._write_manifest(profile_id, manifest)
        return self.guided_status(profile_id)

    def skip_guided(self, profile_id: str, sentence_id: str) -> dict:
        """Marks a sentence as skipped. It's offered again once the rest are done."""
        self._check_sentence(sentence_id)
        manifest = self._read_manifest(profile_id)
        if sentence_id not in manifest["recordings"] and sentence_id not in manifest["skipped"]:
            manifest["skipped"].append(sentence_id)
            self._write_manifest(profile_id, manifest)
        return self.guided_status(profile_id)

    def guided_status(self, profile_id: str) -> dict:
        """Progress so far, and which sentence to show next (so you can resume later)."""
        manifest = self._read_manifest(profile_id)
        recorded = manifest["recordings"]
        not_recorded = [sid for sid, _ in GUIDED_SENTENCES if sid not in recorded]
        fresh = [sid for sid in not_recorded if sid not in manifest["skipped"]]
        # next = first sentence never seen; once those run out, go back to the skipped ones
        next_id = (fresh or not_recorded or [None])[0]
        return {
            "recorded": {sid: {"duration_s": r["duration_s"], "recorded_at": r["recorded_at"]}
                         for sid, r in recorded.items()},
            "skipped": list(manifest["skipped"]),
            "references": list(manifest["references"]),
            "count": len(recorded),
            "total_sentences": len(GUIDED_SENTENCES),
            "total_seconds": round(sum(r["duration_s"] for r in recorded.values()), 1),
            "next_id": next_id,
        }

    def guided_audio_path(self, profile_id: str, sentence_id: str) -> Path:
        self._check_sentence(sentence_id)
        rec = self._read_manifest(profile_id)["recordings"].get(sentence_id)
        path = self._guided_dir(profile_id) / rec["file"] if rec else None
        if not path or not path.is_file():
            raise ProfileNotFound(f"No recording for sentence '{sentence_id}'.")
        return path

    def guided_items(self, profile_id: str) -> list:
        """(sentence_id, exact text, audio file) for every recording, in sentence order."""
        recorded = self._read_manifest(profile_id)["recordings"]
        items = []
        for sid, _ in GUIDED_SENTENCES:
            rec = recorded.get(sid)
            if rec and (self._guided_dir(profile_id) / rec["file"]).is_file():
                items.append((sid, rec["text"], self._guided_dir(profile_id) / rec["file"]))
        return items

    # ---------- guided recordings as cloning references ----------
    def pick_references(self, profile_id: str, target_seconds: float = 30.0, max_clips: int = 10) -> list:
        """
        Chooses guided recordings to use as voice references for zero-shot cloning.
        XTTS joins all reference clips together and listens to the first 30 seconds
        (gpt_cond_len=30 in the Colab notebook), so we take the LONGEST recordings
        (more speech, less silence) until they add up to about 30 seconds.
        """
        recorded = self._read_manifest(profile_id)["recordings"]
        if not recorded:
            raise ProfileError("Record some guided sentences first.")
        longest_first = sorted(recorded.items(), key=lambda kv: kv[1]["duration_s"], reverse=True)
        chosen, total = [], 0.0
        for sid, rec in longest_first[:max_clips]:
            chosen.append(sid)
            total += rec["duration_s"]
            if total >= target_seconds:
                break
        manifest = self._read_manifest(profile_id)
        manifest["references"] = chosen
        self._write_manifest(profile_id, manifest)
        return chosen

    def clear_references(self, profile_id: str):
        manifest = self._read_manifest(profile_id)
        manifest["references"] = []
        self._write_manifest(profile_id, manifest)

    def get_reference_clips(self, profile_id: str) -> list:
        """The chosen guided recordings as raw bytes (skips any that were deleted)."""
        out = []
        for sid in self._read_manifest(profile_id)["references"]:
            try:
                out.append(self.guided_audio_path(profile_id, sid).read_bytes())
            except ProfileNotFound:
                continue
        return out
