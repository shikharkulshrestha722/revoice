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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import requests
import base64
import os
import re

from predictor import get_suggestions
from profiles import ProfileStore, ProfileError, ProfileNotFound

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


# ------------------------------------------------------------
# NO-CACHE for the webpage files.
# Without this the browser sometimes keeps running an OLD app.js
# after we edit it. API replies are left alone.
# ------------------------------------------------------------
API_PATHS = ("/predict", "/speak", "/upload_voice_clip", "/voice_bank",
             "/profiles", "/stock_speakers")


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


@app.post("/predict")
async def predict(text: str = Form("")):
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
async def delete_profile(profile_id: str):
    """
    Deletes a user and ALL their recordings from disk.
    Also asks Colab to forget any voice it built from their clips
    (best effort: if Colab is offline, its memory is wiped on restart anyway).
    """
    store.delete(profile_id)
    print(f"[profiles] deleted {profile_id}")
    try:
        r = requests.post(
            f"{COLAB_API_URL.rstrip('/')}/forget_voices",
            headers={"ngrok-skip-browser-warning": "true"},
            timeout=10,
        )
        colab_cleared = r.status_code == 200
    except requests.exceptions.RequestException:
        colab_cleared = False
    return {"deleted": profile_id, "active": store.active_id(), "colab_cache_cleared": colab_cleared}


@app.get("/stock_speakers")
async def stock_speakers():
    """Built-in XTTS voices, for patients who have no healthy voice left to bank."""
    try:
        r = requests.get(
            f"{COLAB_API_URL.rstrip('/')}/speakers",
            headers={"ngrok-skip-browser-warning": "true"},
            timeout=8,
        )
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


# Voice engines the Colab notebook understands:
#   "xtts" = XTTS-v2 (default), "f5" = F5-TTS
SUPPORTED_ENGINES = ("xtts", "f5")


@app.post("/speak")
async def speak(text: str = Form(...), emotion: str = Form(...), engine: str = Form("xtts"),
                profile_id: str = Form("")):
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
        print(f"[speak:{engine}] {text!r} -> {spoken_text!r} with stock voice {profile['stock_speaker']!r}")
    else:
        clips = [base64.b64encode(c).decode("utf-8") for c in store.get_clips(profile["id"], emotion)]
        if not clips:
            return JSONResponse(
                {"error": f"No voice sample recorded yet for emotion '{emotion}'."},
                status_code=400,
            )
        payload["speaker_wav_base64"] = clips[0]          # kept for older Colab notebooks
        payload["speaker_wav_base64_list"] = clips        # all clips = better cloning
        print(f"[speak:{engine}] {text!r} -> {spoken_text!r} using {len(clips)} clip(s) of {profile['id']}")

    try:
        response = requests.post(
            f"{COLAB_API_URL.rstrip('/')}/synthesize",
            json=payload,
            headers={"ngrok-skip-browser-warning": "true"},  # skips ngrok's free-tier warning page
            timeout=120,  # first generation on Colab can take 20-30 seconds
        )
    except requests.exceptions.RequestException as e:
        return JSONResponse({"error": f"Could not reach Colab API: {e}"}, status_code=502)

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

    return response.json()  # forwards {"audio_base64": "..."} straight to the browser


# ------------------------------------------------------------
# Serve the frontend folder itself (must be LAST so it doesn't
# override the API routes above)
# ------------------------------------------------------------
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")