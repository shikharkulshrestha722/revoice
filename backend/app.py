# ============================================================
# ReVoice — Backend
# ============================================================
# This is the "messenger" between the webpage and Colab.
# It does 3 jobs:
#   1. Serve the frontend webpage
#   2. Save voice bank recordings the patient records
#   3. Forward text + a voice reference to the Colab API, and
#      pass the generated audio back to the webpage
# ============================================================

from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import requests
import base64
import os
import re

from predictor import get_suggestions

app = FastAPI()

# ------------------------------------------------------------
# IMPORTANT: update this every time you restart your Colab notebook
# (ngrok gives a new URL each session unless you're on a paid plan)
# ------------------------------------------------------------
COLAB_API_URL = "https://handcart-retrieval-mulch.ngrok-free.dev"

# ------------------------------------------------------------
# Gemini API key for phrase prediction.
# Get a free key at https://aistudio.google.com/apikey
# Either paste it below, or set it as an environment variable.
# If it's left blank, prediction still works using an offline word list.
# ------------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "PASTE_YOUR_GEMINI_KEY_HERE")
GEMINI_MODEL = "gemini-2.5-flash"


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
    key_is_set = GEMINI_API_KEY and not GEMINI_API_KEY.startswith("PASTE_")
    result = get_suggestions(text, call_gemini=call_gemini if key_is_set else None)
    return result


# In-memory voice bank: { "happy": ["<base64 audio>", ...], ... }
# More clips per emotion = better cloning. We keep the most recent MAX_CLIPS.
# NOTE: this resets when the server restarts; a real deployment would save to disk.
voice_bank = {}
MAX_CLIPS_PER_EMOTION = 3


@app.post("/upload_voice_clip")
async def upload_voice_clip(emotion: str = Form(...), file: UploadFile = None):
    """Receives a recorded voice sample from the browser and adds it to the voice bank."""
    audio_bytes = await file.read()
    clips = voice_bank.setdefault(emotion, [])
    clips.append(base64.b64encode(audio_bytes).decode("utf-8"))
    del clips[:-MAX_CLIPS_PER_EMOTION]     # keep only the most recent few
    return {"status": "saved", "emotion": emotion, "clips": len(clips)}


@app.get("/voice_bank")
async def voice_bank_status():
    """How many clips are stored for each emotion (used by the web page)."""
    return {e: len(c) for e, c in voice_bank.items()}


# Voice engines the Colab notebook understands:
#   "xtts" = XTTS-v2 (default), "f5" = F5-TTS
SUPPORTED_ENGINES = ("xtts", "f5")


@app.post("/speak")
async def speak(text: str = Form(...), emotion: str = Form(...), engine: str = Form("xtts")):
    """
    Takes the composed sentence + chosen emotion, finds the matching
    reference voice clip, and asks the Colab API to generate speech
    with the chosen engine.
    """
    if engine not in SUPPORTED_ENGINES:
        return JSONResponse(
            {"error": f"Unknown engine '{engine}'. Use one of: {', '.join(SUPPORTED_ENGINES)}."},
            status_code=400,
        )

    clips = voice_bank.get(emotion) or []
    if not clips:
        return JSONResponse(
            {"error": f"No voice sample recorded yet for emotion '{emotion}'."},
            status_code=400,
        )

    spoken_text = prepare_text_for_speech(text, emotion)
    print(f"[speak:{engine}] {text!r} -> {spoken_text!r} using {len(clips)} reference clip(s)")

    payload = {
        "text": spoken_text,
        "speaker_wav_base64": clips[0],          # kept for older Colab notebooks
        "speaker_wav_base64_list": clips,        # all clips = better cloning
        "language": "en",
        "emotion": emotion,                      # lets Colab adjust speed/expressiveness
        "engine": engine,                        # which voice model Colab should use
    }

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