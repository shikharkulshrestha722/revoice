# ============================================================
# ReVoice — Parity check: website path vs Colab "Step 4b" path
# ============================================================
# Sends the EXACT bytes of one audio file to Colab two ways and prints
# both similarity scores side by side:
#
#   Step 4b path   the same request the notebook's Step 4b cell builds
#                  (one clip, the fixed test sentence), sent straight to Colab
#   Website path   the backend's real /speak code (generate_speech): a throwaway
#                  user whose ONLY voice is this file, in a temporary folder
#                  (your real users and recordings are not touched)
#   Your profile   (optional, --profile) what the website would send for a real
#                  user right now, with their current "Voice reference" setting
#
# If the first two rows send identical bytes (same md5) but get very different
# scores, the difference is random variation in F5; run with --runs 3 to average.
# If the "Your profile" row sends different clips, that's why the website sounds
# different.
#
# Needs the Colab notebook running (Step 6). Run from the backend folder:
#   py -3.10 parity_check.py "C:\path\to\Recording (2).m4a"
#   py -3.10 parity_check.py data\profiles\shikhar-0d4e\uploads\000001.wav --profile shikhar-0d4e --runs 3
#
# Tip: an uploaded clip is stored as the WAV the BROWSER made from the original file.
# Run the script on the original AND on that WAV: if those two agree, the browser
# conversion didn't change the voice.
# ============================================================

import argparse
import base64
import hashlib
import statistics
import sys
import tempfile
import wave
from pathlib import Path

import app as backend
from profiles import ProfileError, ProfileStore

# exactly the sentence Step 4b uses
TEST_SENTENCE = "Hello, this is a quick test of my cloned voice. Does it sound like me?"


def md5_of_clips(clips_b64):
    """Fingerprint of the reference audio actually sent (decoded bytes, in order)."""
    h = hashlib.md5()
    for c in clips_b64:
        h.update(base64.b64decode(c))
    return h.hexdigest()[:12]


def send_like_step_4b(data: bytes, engine: str) -> tuple:
    """The request Step 4b makes: one clip, neutral, no text clean-up."""
    payload = {"text": TEST_SENTENCE, "language": "en", "emotion": "neutral", "engine": engine,
               "stock_speaker": None, "speaker_wav_base64": None,
               "speaker_wav_base64_list": [base64.b64encode(data).decode()]}
    r = backend.call_colab("post", "/synthesize", json=payload, timeout=180)
    try:
        reply = r.json()
    except ValueError:                                   # e.g. an ngrok error page
        reply = {}
    if r.status_code != 200:
        raise RuntimeError(f"Colab said: {reply.get('error', r.text[:300])}")
    return payload, reply


def send_like_website(store: ProfileStore, profile_id: str, engine: str) -> tuple:
    """The backend's real /speak code, capturing exactly what it sends to Colab."""
    captured = {}
    real_call = backend.call_colab

    def capture(method, path, **kwargs):
        captured["payload"] = kwargs.get("json")
        return real_call(method, path, **kwargs)

    old_store, backend.store, backend.call_colab = backend.store, store, capture
    try:
        info = {}
        result = backend.generate_speech(TEST_SENTENCE, "neutral", engine, profile_id, info)
    finally:
        backend.store, backend.call_colab = old_store, real_call
    if not isinstance(result, dict):                    # a JSONResponse = an error
        raise RuntimeError(result.body.decode()[:300])
    return captured["payload"], result


def throwaway_user(store: ProfileStore, path: Path, data: bytes) -> str:
    """A user whose only voice is this file: as an uploaded recording if it's the kind
    of WAV the website uploads, otherwise as a recorded clip (sent as-is, like
    browser recordings)."""
    p = store.create("Parity check", "cloned", consent_given=True)
    try:
        with wave.open(str(path)) as w:
            seconds = w.getnframes() / w.getframerate()
        store.add_upload(p["id"], data, original_name=path.name, start_s=0, end_s=seconds,
                         speech_s=seconds, consent=True, consented_by="parity check")
        store.set_voice_reference(p["id"], "uploads")
    except (wave.Error, EOFError, ProfileError):
        store.add_clip(p["id"], "neutral", data, path.suffix or ".webm")
        store.set_voice_reference(p["id"], "recorded")
    return p["id"]


def run(label, send, runs):
    sims, rows = [], []
    for i in range(runs):
        try:
            payload, reply = send()
        except Exception as e:                           # keep going: show the other rows
            print(f"  {label}: FAILED - {e}")
            return None
        metrics = reply.get("metrics") or {}
        sims.append(metrics.get("similarity"))
        rows.append(reply)
        print(f"  {label} run {i + 1}: similarity {metrics.get('similarity')}, "
              f"synth {reply.get('synth_seconds')}s")
    ref = rows[-1].get("voice_reference")
    clips = (", ".join(f"{c['name']} ({c['duration_s'] if c['duration_s'] is not None else '?'}s)" for c in ref["clips"]) if ref
             else f"1 clip ({len(base64.b64decode(payload['speaker_wav_base64_list'][0]))} bytes)")
    measured = [s for s in sims if isinstance(s, (int, float))]
    return {"label": label, "md5": md5_of_clips(payload["speaker_wav_base64_list"]),
            "n_clips": len(payload["speaker_wav_base64_list"]), "clips": clips,
            "similarity": round(statistics.mean(measured), 3) if measured else None,
            "spread": (round(min(measured), 3), round(max(measured), 3)) if len(measured) > 1 else None,
            "ref_text": rows[-1].get("ref_text", ""), "text": payload["text"]}


def main():
    parser = argparse.ArgumentParser(description="Compare the website path with Colab's Step 4b path.")
    parser.add_argument("file", help="audio file (wav, m4a, mp3, webm, ogg...)")
    parser.add_argument("--engine", default="f5", choices=backend.SUPPORTED_ENGINES)
    parser.add_argument("--runs", type=int, default=1, help="repeat each path N times (F5 is a bit random)")
    parser.add_argument("--profile", help="also show what the website sends for this real user now")
    args = parser.parse_args()

    path = Path(args.file)
    data = path.read_bytes()
    print(f"File: {path.name}, {len(data)} bytes, md5 {hashlib.md5(data).hexdigest()[:12]}")
    print(f"Colab: {backend.COLAB_API_URL}   engine: {args.engine}   runs per path: {args.runs}\n")

    results = []
    results.append(run("Step 4b path", lambda: send_like_step_4b(data, args.engine), args.runs))
    with tempfile.TemporaryDirectory() as tmp:
        store = ProfileStore(Path(tmp) / "data")
        pid = throwaway_user(store, path, data)
        results.append(run("Website path", lambda: send_like_website(store, pid, args.engine), args.runs))
    if args.profile:
        results.append(run(f"Your profile ({args.profile})",
                           lambda: send_like_website(backend.store, args.profile, args.engine), args.runs))

    print("\n" + "=" * 100)
    print(f"{'path':<32}{'similarity':>11}  {'range':<16}{'ref md5':<14}{'clips sent'}")
    print("-" * 100)
    for r in results:
        if r:
            spread = f"{r['spread'][0]}-{r['spread'][1]}" if r["spread"] else ""
            print(f"{r['label']:<32}{str(r['similarity']):>11}  {spread:<16}{r['md5']:<14}{r['n_clips']}: {r['clips']}")
    print("=" * 100)
    for r in results:
        if r and r["ref_text"]:
            print(f"{r['label']}: F5 heard the reference as {r['ref_text']!r}")
    ok = [r for r in results if r]
    if len(ok) >= 2:
        same_bytes = ok[0]["md5"] == ok[1]["md5"]
        same_text = ok[0]["text"] == ok[1]["text"]
        print(f"\nStep 4b and website sent identical reference bytes: {'YES' if same_bytes else 'NO'}; "
              f"identical text: {'YES' if same_text else 'NO'}")
        if len(ok) == 3 and ok[2]["md5"] != ok[0]["md5"]:
            print(f"'{ok[2]['label']}' sends DIFFERENT audio from this file: {ok[2]['clips']}")
    return 0 if len(ok) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
