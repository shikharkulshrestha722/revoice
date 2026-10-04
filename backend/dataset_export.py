# ============================================================
# ReVoice — Export guided recordings as a training dataset
# ============================================================
# Builds a zip in "LJSpeech" style, the layout coqui-tts reads for
# fine-tuning (checked in coqui-tts 0.27.5, TTS/tts/datasets/formatters.py):
#
#   metadata.csv     one line per clip:   g001|I would like a glass of water, please.|<same text>
#                    (id | text | normalised text; coqui uses the 3rd column; no header line)
#   wavs/g001.wav    22050 Hz mono WAV, if ffmpeg is installed on this computer
#   raw/g001.webm    the original browser recording, if ffmpeg is NOT installed.
#                    The Colab fine-tuning notebook converts raw/ into wavs/.
#   README.txt       what's inside and who it belongs to
# ============================================================

import io
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from profiles import ProfileError

SAMPLE_RATE = 22050   # what XTTS fine-tuning expects


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def convert_to_wav(src: Path, dst: Path):
    """webm/opus -> 22050 Hz mono 16-bit WAV, using ffmpeg."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", str(dst)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg could not convert {src.name}: {result.stderr[-300:]}")


def build_dataset_zip(store, profile_id: str, convert: bool = None) -> bytes:
    """Returns the zip file as bytes. convert=None means 'use ffmpeg if it's installed'."""
    profile = store.get(profile_id)
    items = store.guided_items(profile_id)
    if not items:
        raise ProfileError("No guided recordings to export yet.")
    if convert is None:
        convert = ffmpeg_available()

    metadata_lines = []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf, tempfile.TemporaryDirectory() as tmp:
        for sentence_id, text, audio_path in items:
            text = " ".join(text.replace("|", " ").split())   # "|" would break the columns
            metadata_lines.append(f"{sentence_id}|{text}|{text}")
            if convert:
                wav_path = Path(tmp) / f"{sentence_id}.wav"
                convert_to_wav(audio_path, wav_path)
                zf.write(wav_path, f"wavs/{sentence_id}.wav")
            else:
                zf.write(audio_path, f"raw/{sentence_id}{audio_path.suffix}")

        # "\n" line endings: a "\r" would end up inside the training text
        zf.writestr("metadata.csv", "\n".join(metadata_lines) + "\n")

        total_seconds = sum(store.guided_status(profile_id)["recorded"][sid]["duration_s"]
                            for sid, _, _ in items)
        zf.writestr("README.txt", "\n".join([
            "ReVoice voice banking dataset (LJSpeech layout)",
            "",
            f"Speaker:   {profile['name']}",
            f"Consent:   {'given ' + str(profile.get('consent_at')) if profile.get('consent_given') else 'NOT recorded'}",
            f"Exported:  {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"Clips:     {len(items)}  (about {total_seconds / 60:.1f} minutes)",
            "",
            "metadata.csv: id|text|normalised text  (one line per clip, no header)",
            "wavs/: 22050 Hz mono WAV files" if convert else
            "raw/: original browser recordings (webm/opus). ffmpeg was not installed on the\n"
            "      exporting computer, so convert these to wavs/<id>.wav (22050 Hz mono)\n"
            "      before training. colab/finetune_xtts.ipynb does this automatically.",
            "",
            "This is personal voice data. Store it privately and delete it when no longer needed.",
        ]) + "\n")

    return buffer.getvalue()
