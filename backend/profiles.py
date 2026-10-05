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
#       uploads/              old recordings uploaded for someone who lost their
#                             voice (+ manifest.json: trims, who consented, when)
#
# The currently selected user is remembered in data/settings.json,
# so everything survives a backend restart.
#
# Deleting a profile removes its whole folder: recordings included.
# ============================================================

import io
import json
import re
import secrets
import shutil
import wave
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


# ---- uploaded old recordings (decoded, trimmed and converted to WAV in the browser) ----
UPLOAD_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".webm", ".mp4"}   # original file types
MAX_UPLOAD_BYTES = 8 * 1024 * 1024    # per clip; 2 minutes of 24 kHz mono WAV is about 5.5 MB
MIN_CLIP_SECONDS = 1.0
MAX_CLIP_SECONDS = 120.0
MAX_UPLOADED_CLIPS = 20
MIN_TOTAL_SPEECH_SECONDS = 15.0       # less than this: cloning will probably sound poor
UPLOAD_CONSENT_STATEMENT = ("I am this person or their family/legal guardian and "
                            "have the right to use these recordings.")


class ProfileError(ValueError):
    """A problem the user can fix (bad name, missing consent...). Shown as a 400 error."""


class ProfileNotFound(LookupError):
    """No profile with that id. Shown as a 404 error."""


def wav_duration(data: bytes) -> float:
    """Length in seconds of a mono 16-bit WAV. Raises ProfileError if it isn't one."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError):
        raise ProfileError("The converted audio isn't a valid WAV file.")
    if channels != 1 or width != 2 or not (8000 <= rate <= 48000):
        raise ProfileError("Expected mono 16-bit WAV audio between 8 and 48 kHz.")
    return frames / rate


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

    # ---------- uploaded old recordings ----------
    # For people who already lost their voice: old voice notes / videos, cut in the
    # browser to just their voice and converted there to a clean mono WAV.
    # data/profiles/<id>/uploads/000001.wav ...  + manifest.json with, for each clip:
    # the original file name, the trim, durations, and WHO consented and WHEN.
    def _uploads_dir(self, profile_id: str) -> Path:
        return self._folder(profile_id) / "uploads"

    def _read_uploads(self, profile_id: str) -> dict:
        try:
            m = json.loads((self._uploads_dir(profile_id) / "manifest.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            m = {}
        m.setdefault("clips", [])
        return m

    def _write_uploads(self, profile_id: str, manifest: dict):
        folder = self._uploads_dir(profile_id)
        folder.mkdir(exist_ok=True)
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def add_upload(self, profile_id: str, wav_bytes: bytes, *, original_name: str, start_s: float,
                   end_s: float, speech_s: float, consent: bool, consented_by: str) -> dict:
        """Checks and saves one trimmed recording. Raises ProfileError with a clear reason."""
        if self.get(profile_id)["voice_type"] != "cloned":
            raise ProfileError("This user has a stock voice, so recordings aren't used.")
        consented_by = " ".join((consented_by or "").split())
        if not consent or not consented_by:
            raise ProfileError("Consent is required: tick the box and enter your name.")
        if len(consented_by) > 80:
            raise ProfileError("Name is too long (max 80 characters).")

        extension = Path(original_name or "").suffix.lower()
        if extension not in UPLOAD_EXTENSIONS:
            raise ProfileError(f"'{original_name}' is not a supported file type. "
                               f"Use: {', '.join(sorted(UPLOAD_EXTENSIONS))}.")
        if len(wav_bytes) > MAX_UPLOAD_BYTES:
            raise ProfileError(f"Clip is too big (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB). Trim it shorter.")

        duration = wav_duration(wav_bytes)            # raises ProfileError if not a proper WAV

        # the trim must make sense and match the audio we actually received
        if not (start_s >= 0 and end_s > start_s):
            raise ProfileError("Trim end must be after trim start.")
        length = end_s - start_s
        if not (MIN_CLIP_SECONDS <= length <= MAX_CLIP_SECONDS):
            raise ProfileError(f"Each clip must be {MIN_CLIP_SECONDS:g}-{MAX_CLIP_SECONDS:g} seconds long after trimming.")
        if abs(duration - length) > 0.5:
            raise ProfileError("The audio length doesn't match the trim. Please try again.")
        if not (0 <= speech_s <= duration + 0.1):
            raise ProfileError("Speech time must be between zero and the clip length.")

        manifest = self._read_uploads(profile_id)
        if len(manifest["clips"]) >= MAX_UPLOADED_CLIPS:
            raise ProfileError(f"Up to {MAX_UPLOADED_CLIPS} uploaded clips per user. Delete some first.")
        next_number = max((int(c["id"]) for c in manifest["clips"]), default=0) + 1
        clip_id = f"{next_number:06d}"
        folder = self._uploads_dir(profile_id)
        folder.mkdir(exist_ok=True)
        (folder / f"{clip_id}.wav").write_bytes(wav_bytes)

        clip = {
            "id": clip_id,
            "original_name": Path(original_name).name[:120],
            "trim_start_s": round(start_s, 2),
            "trim_end_s": round(end_s, 2),
            "duration_s": round(duration, 2),
            "speech_s": round(min(speech_s, duration), 2),
            "consented_by": consented_by,
            "consent_statement": UPLOAD_CONSENT_STATEMENT,
            "consented_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        manifest["clips"].append(clip)
        self._write_uploads(profile_id, manifest)
        return clip

    def list_uploads(self, profile_id: str) -> dict:
        clips = self._read_uploads(profile_id)["clips"]
        speech = round(sum(c["speech_s"] for c in clips), 1)
        return {
            "clips": clips,
            "total_seconds": round(sum(c["duration_s"] for c in clips), 1),
            "total_speech_seconds": speech,
            "enough_speech": speech >= MIN_TOTAL_SPEECH_SECONDS,
            "min_total_speech_seconds": MIN_TOTAL_SPEECH_SECONDS,
        }

    def delete_upload(self, profile_id: str, clip_id: str) -> dict:
        manifest = self._read_uploads(profile_id)
        if clip_id not in [c["id"] for c in manifest["clips"]]:
            raise ProfileNotFound(f"No uploaded clip '{clip_id}'.")
        manifest["clips"] = [c for c in manifest["clips"] if c["id"] != clip_id]
        (self._uploads_dir(profile_id) / f"{clip_id}.wav").unlink(missing_ok=True)
        self._write_uploads(profile_id, manifest)
        return self.list_uploads(profile_id)

    def get_upload_clips(self, profile_id: str, target_seconds: float = 45.0, max_clips: int = 6) -> list:
        """Uploaded clips to send to Colab as voice references: the ones with the most
        speech first, until about 45 s (XTTS uses up to 30 s; F5 takes the longest clip).
        Capped so each request through ngrok stays small."""
        clips = sorted(self._read_uploads(profile_id)["clips"], key=lambda c: c["speech_s"], reverse=True)
        out, total = [], 0.0
        for c in clips[:max_clips]:
            path = self._uploads_dir(profile_id) / f"{c['id']}.wav"
            if path.is_file():
                out.append(path.read_bytes())
                total += c["duration_s"]
            if total >= target_seconds:
                break
        return out

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
