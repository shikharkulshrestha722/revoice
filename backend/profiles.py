"""User profiles and their recordings on disk: data/profiles/<id>/ holds profile.json,
clips/<emotion>/, guided/ and uploads/; data/settings.json remembers the selected user."""

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

# Ids become folder names, so only safe characters (blocks paths like "../../windows").
SAFE_ID = re.compile(r"^[a-z0-9-]{1,64}$")
AUDIO_EXTENSIONS = (".webm", ".wav", ".ogg", ".mp3", ".m4a")


# Uploaded old recordings (the browser converts them to WAV).
UPLOAD_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".webm", ".mp4"}   # original file types
MAX_UPLOAD_BYTES = 8 * 1024 * 1024    # per clip; 2 minutes of 24 kHz mono WAV is about 5.5 MB
MIN_CLIP_SECONDS = 1.0
MAX_CLIP_SECONDS = 120.0
MAX_UPLOADED_CLIPS = 20
MIN_TOTAL_SPEECH_SECONDS = 15.0       # below this, cloning will probably sound poor
UPLOAD_CONSENT_STATEMENT = ("I am this person or their family/legal guardian and "
                            "have the right to use these recordings.")


class ProfileError(ValueError):
    """A problem the user can fix; shown as a 400 error."""


class ProfileNotFound(LookupError):
    """Unknown profile or clip; shown as a 404 error."""


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


# "Voice reference" setting: which recordings /speak copies the voice from.
REFERENCE_MODES = {
    "auto": "Auto",                              # emotion clips > uploads > guided takes
    "uploads": "Uploaded recordings only",
    "recorded": "Recorded clips only",
    "guided": "Guided takes only",
}


def _ebml_number(data: bytes, i: int, keep_marker=False):
    """Reads one EBML variable-length number (WebM's building block).
    Returns (value, bytes used, whether it means "unknown size")."""
    first = data[i]
    length, mask = 1, 0x80
    while length <= 8 and not first & mask:
        mask >>= 1
        length += 1
    if length > 8:
        raise ValueError("bad EBML number")
    value = first if keep_marker else first & (mask - 1)
    unknown = (first & (mask - 1)) == mask - 1
    for k in range(1, length):
        value = (value << 8) | data[i + k]
        unknown = unknown and data[i + k] == 0xFF
    return value, length, unknown


def webm_duration(data: bytes):
    """Length in seconds of a MediaRecorder WebM, or None.
    Browsers don't store the length, so it's read from the last audio block's time stamp (~20 ms)."""
    scale_ns, cluster_ms, last_ms = 1_000_000, 0, None
    containers = {0x18538067, 0x1F43B675, 0x1549A966, 0xA0}   # Segment, Cluster, Info, BlockGroup
    i = 0
    try:
        while i < len(data):
            element, n, _ = _ebml_number(data, i, keep_marker=True)
            i += n
            size, n, unknown = _ebml_number(data, i)
            i += n
            if element in containers:
                continue                                    # step into it
            body = data[i:i + size]
            if element == 0x2AD7B1:                         # TimecodeScale
                scale_ns = int.from_bytes(body, "big")
            elif element == 0xE7:                           # Cluster timecode
                cluster_ms = int.from_bytes(body, "big")
            elif element in (0xA3, 0xA1):                   # SimpleBlock / Block
                _, tn, _ = _ebml_number(data, i)
                block_ms = cluster_ms + int.from_bytes(data[i + tn:i + tn + 2], "big", signed=True)
                last_ms = block_ms if last_ms is None else max(last_ms, block_ms)
            if unknown:
                break
            i += size
    except (ValueError, IndexError):
        pass                                                # damaged file: use what was read
    return None if last_ms is None else round(last_ms * scale_ns / 1e9 + 0.02, 2)


def clip_duration(path: Path):
    """Length in seconds of a stored clip (WAV or browser WebM), or None if unknown."""
    data = path.read_bytes()
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(io.BytesIO(data)) as w:
                return round(w.getnframes() / w.getframerate(), 2)
        except (wave.Error, EOFError, ZeroDivisionError):
            return None
    if path.suffix.lower() == ".webm":
        return webm_duration(data)
    return None


def make_profile_id(name: str) -> str:
    """'Mr Sharma' -> 'mr-sharma-3fa2': readable, with a random suffix so ids never clash."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "user"
    return f"{slug}-{secrets.token_hex(2)}"


class ProfileStore:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.profiles_dir = self.data_dir / "profiles"
        self.settings_file = self.data_dir / "settings.json"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

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

    # Profiles
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
        profile.setdefault("voice_reference", "auto")     # older profiles don't have it
        profile.setdefault("reference_clip", "")          # one chosen upload id; "" = all uploads
        # the caregiver PIN's hash never leaves the server: callers only learn whether one is set
        profile["pin_set"] = bool(profile.pop("pin", None))
        profile["clip_counts"] = self.clip_counts(profile_id)
        return profile

    # Caregiver PIN (caregiver_pin.py): only the salted hash record is stored, never the PIN
    def pin_record(self, profile_id: str):
        path = self._folder(profile_id) / "profile.json"
        return json.loads(path.read_text(encoding="utf-8")).get("pin")

    def set_pin_record(self, profile_id: str, record: dict):
        path = self._folder(profile_id) / "profile.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["pin"] = record
        path.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    def set_voice_reference(self, profile_id: str, mode: str, clip_id: str = "") -> dict:
        """Sets which recordings /speak copies the voice from (see REFERENCE_MODES).
        With "uploads", clip_id picks one uploaded clip ("" = all)."""
        if mode not in REFERENCE_MODES:
            raise ProfileError(f"Unknown voice reference '{mode}'. Use one of: {', '.join(REFERENCE_MODES)}.")
        if clip_id:
            if mode != "uploads":
                raise ProfileError("A specific clip can only be chosen with 'Uploaded recordings only'.")
            if clip_id not in [c["id"] for c in self._read_uploads(profile_id)["clips"]]:
                raise ProfileNotFound(f"No uploaded clip '{clip_id}'.")
        path = self._folder(profile_id) / "profile.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["voice_reference"] = mode
        profile["reference_clip"] = clip_id
        path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        return self.get(profile_id)

    def list(self) -> list:
        """All profiles, oldest first."""
        out = []
        for folder in self.profiles_dir.iterdir():
            if (folder / "profile.json").is_file():
                try:
                    out.append(self.get(folder.name))
                except (ProfileNotFound, ValueError):
                    continue   # skip a broken folder instead of failing the whole list
        return sorted(out, key=lambda p: p["created_at"])

    def delete(self, profile_id: str):
        """Removes the profile and all its recordings from disk."""
        folder = self._folder(profile_id)
        shutil.rmtree(folder)
        if self.active_id() is None:
            # the selected user was deleted: select the oldest remaining one
            remaining = self.list()
            self._write_settings({"active_profile": remaining[0]["id"] if remaining else None})

    # Selected user
    def active_id(self):
        """Id of the selected profile, or None (none selected, or it was deleted)."""
        pid = self._read_settings().get("active_profile")
        try:
            self._folder(pid)
            return pid
        except ProfileNotFound:
            return None

    def select(self, profile_id: str) -> dict:
        profile = self.get(profile_id)          # raises if missing
        self._write_settings({"active_profile": profile_id})
        return profile

    # Recorded emotion clips
    def add_clip(self, profile_id: str, emotion: str, audio: bytes, extension: str = ".webm") -> int:
        """Saves a recording, keeps the newest MAX_CLIPS_PER_EMOTION, returns the clip count."""
        if self.get(profile_id)["voice_type"] != "cloned":
            raise ProfileError("This user has a stock voice, so recordings aren't used.")
        if not audio:
            raise ProfileError("The recording was empty.")
        extension = extension.lower() if extension.lower() in AUDIO_EXTENSIONS else ".webm"

        folder = self._clip_folder(profile_id, emotion)
        folder.mkdir(parents=True, exist_ok=True)
        # Numbered by a counter, not the clock: Windows' clock is too coarse and names could clash.
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
        """Recordings for one emotion as raw bytes, oldest first."""
        return [f.read_bytes() for f in self._clip_files(self._clip_folder(profile_id, emotion))]

    # Voice references sent to Colab: dicts of source, name, duration_s, data.
    def _reference(self, source: str, name: str, path: Path, duration=None) -> dict:
        measured = clip_duration(path)
        return {"source": source, "name": name, "data": path.read_bytes(),
                "duration_s": measured if measured is not None else duration}

    def emotion_references(self, profile_id: str, emotion: str) -> list:
        folder = self._clip_folder(profile_id, emotion)
        return [self._reference("emotion_clips", f"{emotion}/{f.name}", f) for f in self._clip_files(folder)]

    def choose_references(self, profile_id: str, emotion: str) -> tuple:
        """Recordings for /speak per the Voice reference setting: (mode, source, references).
        A forced kind with no recordings raises ProfileError; other recordings are never swapped in."""
        profile = self.get(profile_id)
        mode = profile["voice_reference"]
        finders = {
            "recorded": ("emotion_clips", lambda: self.emotion_references(profile_id, emotion)),
            "uploads": ("uploaded_recordings",
                        lambda: self.upload_references(profile_id, only=profile["reference_clip"])),
            "guided": ("guided_references", lambda: self.guided_references(profile_id)),
        }
        if mode == "auto":
            for source, find in finders.values():          # recorded, then uploads, then guided
                refs = find()
                if refs:
                    return mode, source, refs
            raise ProfileError(f"No voice sample recorded yet for emotion '{emotion}'. "
                               "Record one, upload old recordings, or use guided recordings as voice references.")
        source, find = finders[mode]
        refs = find()
        if mode == "uploads" and profile["reference_clip"] and not refs:
            # the chosen clip was deleted: never pick another upload instead
            raise ProfileError(f"The chosen uploaded clip (#{profile['reference_clip']}) no longer exists. "
                               "Choose another clip in Voice reference.")
        if not refs:
            missing = {
                "recorded": f"no recorded clip for the emotion '{emotion}'",
                "uploads": "no uploaded recordings",
                "guided": "no guided takes chosen as voice references",
            }[mode]
            raise ProfileError(f"Voice reference is set to '{REFERENCE_MODES[mode]}', but {profile['name']} "
                               f"has {missing}. Add some, or change the Voice reference setting.")
        return mode, source, refs

    def clip_counts(self, profile_id: str) -> dict:
        clips_root = self._folder(profile_id) / "clips"
        return {e: len(self._clip_files(clips_root / e))
                for e in EMOTIONS if self._clip_files(clips_root / e)}

    # Guided voice banking: guided/<id>.webm + manifest.json (exact text, length, skips, references)
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
        """Saves the recording of one sentence, replacing any previous take."""
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
        for old in folder.glob(f"{sentence_id}.*"):      # remove the previous take
            if old.suffix.lower() in AUDIO_EXTENSIONS:
                old.unlink()
        (folder / f"{sentence_id}{extension}").write_bytes(audio)

        manifest = self._read_manifest(profile_id)
        manifest["recordings"][sentence_id] = {
            "file": f"{sentence_id}{extension}",
            "text": text,                      # exact words, needed for fine-tuning
            "duration_s": round(float(duration_s), 2),
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if sentence_id in manifest["skipped"]:
            manifest["skipped"].remove(sentence_id)
        self._write_manifest(profile_id, manifest)
        return self.guided_status(profile_id)

    def skip_guided(self, profile_id: str, sentence_id: str) -> dict:
        """Marks a sentence as skipped; it's offered again after the rest."""
        self._check_sentence(sentence_id)
        manifest = self._read_manifest(profile_id)
        if sentence_id not in manifest["recordings"] and sentence_id not in manifest["skipped"]:
            manifest["skipped"].append(sentence_id)
            self._write_manifest(profile_id, manifest)
        return self.guided_status(profile_id)

    def guided_status(self, profile_id: str) -> dict:
        """Progress so far and the next sentence to show."""
        manifest = self._read_manifest(profile_id)
        recorded = manifest["recordings"]
        not_recorded = [sid for sid, _ in GUIDED_SENTENCES if sid not in recorded]
        fresh = [sid for sid in not_recorded if sid not in manifest["skipped"]]
        # first unseen sentence; skipped ones come back once those run out
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

    def pick_references(self, profile_id: str, target_seconds: float = 30.0, max_clips: int = 10) -> list:
        """Chooses guided takes as voice references: the longest, up to about 30 s,
        because XTTS uses the first 30 s of the joined clips (gpt_cond_len=30)."""
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

    # Uploaded old recordings: uploads/<id>.wav + manifest.json (name, trim, lengths, consent)
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
        """Validates and saves one trimmed recording; raises ProfileError with the reason."""
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

        duration = wav_duration(wav_bytes)            # raises ProfileError if not a valid WAV

        # the trim must be valid and match the audio received
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

    def upload_references(self, profile_id: str, target_seconds: float = 45.0, max_clips: int = 6,
                          only: str = "") -> list:
        """Uploads as voice references, most speech first, up to ~45 s and max_clips (keeps requests small).
        only = one clip id: just that clip, or nothing if it was deleted."""
        clips = sorted(self._read_uploads(profile_id)["clips"], key=lambda c: c["speech_s"], reverse=True)
        if only:
            clips = [c for c in clips if c["id"] == only]
        out, total = [], 0.0
        for c in clips[:max_clips]:
            path = self._uploads_dir(profile_id) / f"{c['id']}.wav"
            if path.is_file():
                ref = self._reference("uploaded_recordings", f"{c['original_name']} (#{c['id']})",
                                      path, c["duration_s"])
                ref["speech_s"] = c["speech_s"]
                out.append(ref)
                total += c["duration_s"]
            if total >= target_seconds:
                break
        return out

    def get_upload_clips(self, profile_id: str, target_seconds: float = 45.0, max_clips: int = 6) -> list:
        """upload_references as raw bytes."""
        return [r["data"] for r in self.upload_references(profile_id, target_seconds, max_clips)]

    def clear_references(self, profile_id: str):
        manifest = self._read_manifest(profile_id)
        manifest["references"] = []
        self._write_manifest(profile_id, manifest)

    def guided_references(self, profile_id: str) -> list:
        """Guided takes chosen as voice references, skipping deleted ones."""
        manifest = self._read_manifest(profile_id)
        out = []
        for sid in manifest["references"]:
            try:
                path = self.guided_audio_path(profile_id, sid)
            except ProfileNotFound:
                continue
            out.append(self._reference("guided_references", path.name, path,
                                       manifest["recordings"][sid]["duration_s"]))
        return out

    def get_reference_clips(self, profile_id: str) -> list:
        """guided_references as raw bytes."""
        return [r["data"] for r in self.guided_references(profile_id)]
