# ============================================================
# ReVoice — Backend
# ============================================================
# This is the "messenger" between the webpage and Colab.
# Its jobs:
#   1. Serve the frontend webpage
#   2. Manage user profiles and save their voice recordings on disk
#   3. Forward text + the user's voice to the Colab API, and
#      pass the generated audio back to the webpage
#   4. Ask Gemini for phrase predictions
# ============================================================

from fastapi import FastAPI, UploadFile, Form, Request
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import requests
import base64
import json
import os
import re
import time

from predictor import get_suggestions
from profiles import ProfileStore, ProfileError, ProfileNotFound, MAX_UPLOAD_BYTES, REFERENCE_MODES
from guided_sentences import GUIDED_SENTENCES, SESSION_SIZE
from dataset_export import build_dataset_zip
from generation_log import log_generation, summarize

# ------------------------------------------------------------
# SETTINGS come from backend/.env (copy .env.example to .env).
# Keeping keys in .env means they never end up in git.
# Real environment variables win over .env if both are set.
# ------------------------------------------------------------
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Public URL of the Colab voice server (ngrok). Our free ngrok domain is
# static, so this normally never changes.
COLAB_API_URL = os.environ.get("COLAB_API_URL", "https://handcart-retrieval-mulch.ngrok-free.dev")

# Gemini API key for phrase prediction (free key: https://aistudio.google.com/apikey).
# If it's blank, prediction still works using an offline word list.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

app = FastAPI()


def call_colab(method: str, path: str, **kwargs):
    """
    Sends a request to the Colab voice server (through ngrok).
    If the CONNECTION fails (dropped connection, SSL hiccup on the tunnel) we wait
    a second and try ONE more time. We do NOT retry when Colab replies with an
    error (e.g. HTTP 500) or takes too long to answer: it would fail the same way,
    or do the same slow work twice on the GPU.
    """
    send = requests.post if method == "post" else requests.get
    url = f"{COLAB_API_URL.rstrip('/')}{path}"
    headers = {"ngrok-skip-browser-warning": "true"}   # skips ngrok's free-tier warning page
    try:
        return send(url, headers=headers, **kwargs)
    except requests.exceptions.ConnectionError as e:   # SSL errors are connection errors too
        print(f"[colab] {path}: connection problem ({type(e).__name__}), retrying once...")
        time.sleep(1)
        return send(url, headers=headers, **kwargs)


# ------------------------------------------------------------
# NO-CACHE for the webpage files.
# Without this the browser sometimes keeps running an OLD app.js
# after we edit it. API replies are left alone.
# ------------------------------------------------------------
API_PATHS = ("/predict", "/speak", "/upload_voice_clip", "/voice_bank",
             "/profiles", "/stock_speakers", "/guided", "/stats", "/uploads")


@app.middleware("http")
async def no_cache_for_frontend(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith(API_PATHS):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


def call_gemini(prompt: str) -> str:
    """Sends the prompt to Gemini and returns the raw text reply."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 200},
    }
    r = requests.post(
        url,
        json=body,
        headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
        timeout=8,
    )
    r.raise_for_status()
    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def prepare_text_for_speech(text: str, emotion: str = "neutral") -> str:
    """
    XTTS reads ALL CAPS badly (shouting, or spelling letters out).
    Convert to normal sentence case and make sure it ends with punctuation,
    which also gives the model better prosody.
    """
    text = " ".join(text.split())          # tidy spacing
    if not text:
        return text

    # only lowercase if it's mostly caps (don't ruin text the user typed properly)
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        text = text.lower()

    text = text[0].upper() + text[1:]      # capitalise first letter
    text = re.sub(r"\bi\b", "I", text)     # standalone "i" -> "I"

    # punctuation strongly shapes XTTS delivery, so match it to the emotion
    if text[-1] not in ".!?":
        text += "!" if emotion in ("urgent", "happy") else "."
    return text


# NOTE: endpoints that wait for Colab or Gemini are plain "def", not "async def".
# FastAPI then runs each one on its own worker thread, so a 30-second speech
# generation doesn't freeze everything else (like the prediction bar).
@app.post("/predict")
def predict(text: str = Form("")):
    """Suggests 3 likely sentences based on what has been typed so far."""
    key_is_set = bool(GEMINI_API_KEY) and not GEMINI_API_KEY.startswith("PASTE_")
    result = get_suggestions(text, call_gemini=call_gemini if key_is_set else None)
    return result


# ------------------------------------------------------------
# USER PROFILES + VOICE BANK (saved on disk, see profiles.py)
# ------------------------------------------------------------
# Data folder can be moved with REVOICE_DATA_DIR in .env; default is backend/data
DATA_DIR = os.environ.get("REVOICE_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
store = ProfileStore(DATA_DIR)
GENERATION_LOG = os.path.join(DATA_DIR, "generation_log.csv")   # one line per /speak call

# Used only if Colab is offline when the "Add user" panel asks for the list.
# The live list comes from the Colab notebook (GET /speakers).
FALLBACK_STOCK_SPEAKERS = [
    "Ana Florence", "Claribel Dervla", "Daisy Studious", "Gracie Wise",
    "Andrew Chipper", "Craig Gutsy", "Damien Black", "Viktor Eka",
]


# Turn our own errors into clear messages for the web page
@app.exception_handler(ProfileError)
async def profile_error(request: Request, exc: ProfileError):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(ProfileNotFound)
async def profile_not_found(request: Request, exc: ProfileNotFound):
    return JSONResponse({"error": str(exc)}, status_code=404)


def resolve_profile(profile_id: str = "") -> dict:
    """The profile a request is about: the one named, or else the selected one."""
    profile_id = profile_id or store.active_id()
    if not profile_id:
        raise ProfileError("No user selected. Click '+ Add user' first.")
    return store.get(profile_id)


@app.get("/profiles")
async def list_profiles():
    """All users, plus which one is selected."""
    return {"profiles": store.list(), "active": store.active_id()}


@app.post("/profiles")
async def create_profile(
    name: str = Form(...),
    voice_type: str = Form("cloned"),
    stock_speaker: str = Form(""),
    consent_given: bool = Form(False),
):
    """Adds a user and selects them straight away."""
    profile = store.create(name, voice_type, stock_speaker, consent_given)
    store.select(profile["id"])
    print(f"[profiles] created {profile['id']} ({voice_type})")
    return store.get(profile["id"])


@app.post("/profiles/{profile_id}/select")
async def select_profile(profile_id: str):
    return store.select(profile_id)


@app.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str):
    """
    Deletes a user and ALL their recordings from disk.
    Also asks Colab to forget any voice it built from their clips
    (best effort: if Colab is offline, its memory is wiped on restart anyway).
    """
    store.delete(profile_id)
    print(f"[profiles] deleted {profile_id}")
    try:
        r = call_colab("post", "/forget_voices", timeout=10)
        colab_cleared = r.status_code == 200
    except requests.exceptions.RequestException:
        colab_cleared = False
    return {"deleted": profile_id, "active": store.active_id(), "colab_cache_cleared": colab_cleared}


@app.get("/stock_speakers")
def stock_speakers():
    """Built-in XTTS voices, for patients who have no healthy voice left to bank."""
    try:
        r = call_colab("get", "/speakers", timeout=8)
        r.raise_for_status()
        speakers = r.json().get("speakers") or []
        if speakers:
            return {"speakers": speakers, "source": "colab"}
    except (requests.exceptions.RequestException, ValueError):
        pass
    return {"speakers": FALLBACK_STOCK_SPEAKERS, "source": "fallback"}


@app.post("/upload_voice_clip")
async def upload_voice_clip(emotion: str = Form(...), file: UploadFile = None,
                            profile_id: str = Form("")):
    """Receives a recorded voice sample and saves it in the user's voice bank on disk."""
    if file is None:
        raise ProfileError("No audio file was sent.")
    profile = resolve_profile(profile_id)
    extension = os.path.splitext(file.filename or "")[1] or ".webm"
    count = store.add_clip(profile["id"], emotion, await file.read(), extension)
    return {"status": "saved", "profile": profile["id"], "emotion": emotion, "clips": count}


@app.get("/voice_bank")
async def voice_bank_status(profile_id: str = ""):
    """How many clips the user has for each emotion (used by the web page)."""
    if not (profile_id or store.active_id()):
        return {}
    return resolve_profile(profile_id)["clip_counts"]


# ------------------------------------------------------------
# GUIDED VOICE BANKING
# The person reads sentences one by one; each clip is saved with
# its exact words (training data for fine-tuning, see Phase 3).
# ------------------------------------------------------------
@app.get("/guided/sentences")
async def guided_sentences():
    return {"sentences": [{"id": sid, "text": text} for sid, text in GUIDED_SENTENCES],
            "session_size": SESSION_SIZE}


@app.get("/guided/status")
async def guided_status(profile_id: str = ""):
    """Progress, total minutes, and which sentence to show next (resume later)."""
    return store.guided_status(resolve_profile(profile_id)["id"])


@app.post("/guided/record")
async def guided_record(sentence_id: str = Form(...), duration_s: float = Form(...),
                        file: UploadFile = None, profile_id: str = Form("")):
    """Saves one guided recording. Recording the same sentence again = Redo."""
    if file is None:
        raise ProfileError("No audio file was sent.")
    profile = resolve_profile(profile_id)
    extension = os.path.splitext(file.filename or "")[1] or ".webm"
    return store.save_guided(profile["id"], sentence_id, await file.read(), extension, duration_s)


@app.post("/guided/skip")
async def guided_skip(sentence_id: str = Form(...), profile_id: str = Form("")):
    return store.skip_guided(resolve_profile(profile_id)["id"], sentence_id)


@app.get("/guided/audio/{sentence_id}")
async def guided_audio(sentence_id: str, profile_id: str = ""):
    """Plays back a saved guided recording."""
    path = store.guided_audio_path(resolve_profile(profile_id)["id"], sentence_id)
    media = {".webm": "audio/webm", ".wav": "audio/wav", ".ogg": "audio/ogg",
             ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})


@app.post("/guided/references")
async def guided_use_as_references(profile_id: str = Form("")):
    """Use the best guided recordings as voice references for cloning."""
    pid = resolve_profile(profile_id)["id"]
    store.pick_references(pid)
    return store.guided_status(pid)


@app.delete("/guided/references")
async def guided_clear_references(profile_id: str = ""):
    pid = resolve_profile(profile_id)["id"]
    store.clear_references(pid)
    return store.guided_status(pid)


# ------------------------------------------------------------
# UPLOADED OLD RECORDINGS
# For someone who has already lost their voice: old voice notes or videos.
# The browser decodes the file, the caregiver trims out other people's
# voices, and the browser sends a clean mono WAV of just that part.
# ------------------------------------------------------------
@app.get("/uploads")
async def list_uploads(profile_id: str = ""):
    return store.list_uploads(resolve_profile(profile_id)["id"])


@app.post("/uploads")
async def upload_recording(file: UploadFile = None, profile_id: str = Form(""),
                           original_name: str = Form(""), start_s: float = Form(...),
                           end_s: float = Form(...), speech_s: float = Form(0.0),
                           consent: bool = Form(False), consented_by: str = Form("")):
    """Saves one trimmed clip (checked in profiles.add_upload) and returns the updated list."""
    if file is None:
        raise ProfileError("No audio file was sent.")
    pid = resolve_profile(profile_id)["id"]
    data = await file.read(MAX_UPLOAD_BYTES + 1)     # never read more than the limit + 1 byte
    clip = store.add_upload(pid, data, original_name=original_name, start_s=start_s, end_s=end_s,
                            speech_s=speech_s, consent=consent, consented_by=consented_by)
    print(f"[uploads] {pid}: saved {clip['original_name']} ({clip['duration_s']}s), consent by {clip['consented_by']}")
    return {"saved": clip, **store.list_uploads(pid)}


@app.delete("/uploads/{clip_id}")
async def delete_uploaded_recording(clip_id: str, profile_id: str = ""):
    return store.delete_upload(resolve_profile(profile_id)["id"], clip_id)


@app.get("/profiles/{profile_id}/export")
async def export_dataset(profile_id: str):
    """Downloads the user's guided recordings as an LJSpeech-style zip."""
    data = build_dataset_zip(store, profile_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="revoice_dataset_{profile_id}.zip"'},
    )


INPUT_METHODS = ("pointer", "keyboard", "mixed")     # how the sentence was entered (for the log)

# Voice engines the Colab notebook understands:
#   "xtts" = XTTS-v2 (default), "f5" = F5-TTS
SUPPORTED_ENGINES = ("xtts", "f5")


@app.post("/speak")
def speak(text: str = Form(...), emotion: str = Form(...), engine: str = Form("xtts"),
                profile_id: str = Form(""), selections: str = Form(""), utterance_id: str = Form(""),
                input_method: str = Form("")):
    """
    Generates speech (see generate_speech below) and writes one line to
    data/generation_log.csv for EVERY call, whether it worked or not.
    selections / utterance_id come from the page's selection counter; input_method says
    whether the sentence was pointed (pointer), typed (keyboard) or both (mixed).
    The text is spoken exactly the same way however it was entered.
    """
    info = {}            # generate_speech fills in voice_source, voice_reference and seconds
    try:
        result = generate_speech(text, emotion, engine, profile_id, info)
        error = json.loads(result.body)["error"] if isinstance(result, JSONResponse) else ""
    except Exception as e:                # e.g. no user selected; logged, then shown as usual
        result, error = e, str(e)
    reply = result if isinstance(result, dict) else {}   # Colab's reply when it worked

    log_generation(
        GENERATION_LOG, text=text, engine=engine, emotion=emotion,
        voice_source=info.get("voice_source", ""), seconds=info.get("seconds"),
        success=not error, error=error,
        selections=int(selections) if selections.strip().isdigit() else None,
        utterance_id=utterance_id[:40],
        input_method=input_method if input_method in INPUT_METHODS else "",
        synth_seconds=reply.get("synth_seconds"), metrics=reply.get("metrics"),
        voice_reference=info.get("voice_reference"), ref_text=reply.get("ref_text"),
    )
    if isinstance(result, Exception):
        raise result
    return result


def generate_speech(text, emotion, engine, profile_id, info):
    """
    Takes the composed sentence + chosen emotion, finds the user's
    voice (their recordings, or a stock voice), and asks the Colab API
    to generate speech with the chosen engine.
    """
    if engine not in SUPPORTED_ENGINES:
        return JSONResponse(
            {"error": f"Unknown engine '{engine}'. Use one of: {', '.join(SUPPORTED_ENGINES)}."},
            status_code=400,
        )

    profile = resolve_profile(profile_id)
    spoken_text = prepare_text_for_speech(text, emotion)
    payload = {
        "text": spoken_text,
        "language": "en",
        "emotion": emotion,                      # lets Colab adjust speed/expressiveness
        "engine": engine,                        # which voice model Colab should use
    }

    if profile["voice_type"] == "stock":
        # stock voice: no recordings, Colab uses one of XTTS's built-in speakers
        if engine != "xtts":
            return JSONResponse(
                {"error": "Stock voices only work with XTTS-v2 (F5-TTS needs a recorded voice)."},
                status_code=400,
            )
        payload["stock_speaker"] = profile["stock_speaker"]
        info["voice_source"] = "stock"
        print(f"[speak:{engine}] {text!r} -> {spoken_text!r} with stock voice {profile['stock_speaker']!r}")
    else:
        # Which recordings to copy the voice from: the user's "Voice reference" setting
        # (Auto = clips recorded for this emotion > uploaded old recordings > guided takes).
        # Raises a clear error if the chosen kind has none (never a silent swap).
        mode, source, refs = store.choose_references(profile["id"], emotion)
        refs = clips_for_engine(engine, refs)
        info["voice_source"] = source
        info["voice_reference"] = voice_reference_info(mode, source, refs, engine)
        clips = [base64.b64encode(r["data"]).decode("utf-8") for r in refs]
        # exactly the fields Colab's Step 4b test sends (it has no stock voice and
        # puts every clip in the list), so website and Step 4b results are comparable
        payload["stock_speaker"] = None
        payload["speaker_wav_base64"] = None
        payload["speaker_wav_base64_list"] = clips
        sent = ", ".join(f"{r['name']} ({r['duration_s'] if r['duration_s'] is not None else '?'}s)" for r in refs)
        print(f"[speak:{engine}] {text!r} -> {spoken_text!r} using {source} [{mode}]: {sent}")

    start = time.perf_counter()
    try:
        # first generation on Colab can take 20-30 seconds
        response = call_colab("post", "/synthesize", json=payload, timeout=120)
    except requests.exceptions.RequestException as e:
        info["seconds"] = time.perf_counter() - start
        return JSONResponse({"error": f"Could not reach Colab API: {e}"}, status_code=502)

    info["seconds"] = time.perf_counter() - start    # generation time, including the network

    # If Colab returned an error, show WHAT went wrong instead of just the status code
    if response.status_code != 200:
        try:
            detail = response.json().get("error", response.text[:500])
        except ValueError:
            detail = response.text[:500]
        print(f"[Colab error {response.status_code}] {detail}")   # also shows in your terminal
        return JSONResponse(
            {"error": f"Colab said: {detail}"},
            status_code=502,
        )

    reply = response.json()    # {"audio_base64": "...", metrics...} goes straight to the browser,
    if "voice_reference" in info:          # plus exactly which recordings were sent
        reply["voice_reference"] = info["voice_reference"]
    return reply


def clips_for_engine(engine, refs):
    """F5 copies the voice from ONE clip. If we sent several, Colab would pick the longest
    by itself AND measure similarity against all of them joined together, i.e. partly
    against audio F5 never heard. So F5 gets just one clip: the first in our order
    (uploads: most speech first; guided: longest first), or for recorded emotion clips
    the longest. XTTS really uses all the clips, so it gets all of them."""
    if engine != "f5" or len(refs) <= 1:
        return refs
    if refs[0]["source"] == "emotion_clips":
        return [max(refs, key=lambda r: r["duration_s"] or 0)]      # ties: the oldest
    return refs[:1]


def voice_reference_info(mode, source, refs, engine="") -> dict:
    """What the page shows (and the log records) about the recordings sent to Colab."""
    return {
        "mode": mode,
        "mode_label": REFERENCE_MODES[mode],
        "engine": engine,
        "source": source,
        "clips": [{"source": r["source"], "name": r["name"], "duration_s": r["duration_s"]} for r in refs],
        "total_seconds": round(sum(r["duration_s"] or 0 for r in refs), 2),
    }


@app.post("/profiles/{profile_id}/voice_reference")
async def set_voice_reference(profile_id: str, mode: str = Form(...), clip_id: str = Form("")):
    """Caregiver setting: which recordings to copy the voice from (see REFERENCE_MODES);
    with "uploads", clip_id can pick one specific uploaded clip."""
    return store.set_voice_reference(profile_id, mode, clip_id)


@app.get("/voice_bank/references")
async def preview_references(emotion: str = "neutral", profile_id: str = "", engine: str = "f5"):
    """Which recordings the NEXT /speak would send, so the page can show it before
    generating. Returns {"error": ...} (status 200) when the setting can't be met."""
    profile = resolve_profile(profile_id)
    if profile["voice_type"] == "stock":
        return {"mode": "stock", "clips": []}
    try:
        mode, source, refs = store.choose_references(profile["id"], emotion)
        return voice_reference_info(mode, source, clips_for_engine(engine, refs), engine)
    except ProfileError as e:
        return {"mode": profile["voice_reference"], "mode_label": REFERENCE_MODES[profile["voice_reference"]],
                "clips": [], "error": str(e)}


@app.get("/stats/summary")
async def stats_summary():
    """Average generation time per engine and selections saved (from the log)."""
    return summarize(GENERATION_LOG)


# ------------------------------------------------------------
# Serve the frontend folder itself (must be LAST so it doesn't
# override the API routes above)
# ------------------------------------------------------------
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")