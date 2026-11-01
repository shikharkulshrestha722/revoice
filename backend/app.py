"""ReVoice backend: serves the page, stores profiles and recordings, and relays to Gemini and Colab."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, Form, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel
import asyncio
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
from switch_bridge import SwitchBridge, SwitchHub, VALID_STATES
from caregiver_pin import (AccessDenied, PinGuard, TOKEN_HEADER, TOKEN_IDLE_S, audit, check_new_pin,
                           hash_pin, read_audit)
from indic_languages import (LANGUAGES, TEXT_MODES, ConversionError, convert_text, finish_indic_text,
                             in_target_script, language_list)

# Settings come from backend/.env (kept out of git); real environment variables win.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Static ngrok domain of the Colab voice server.
COLAB_API_URL = os.environ.get("COLAB_API_URL", "https://handcart-retrieval-mulch.ngrok-free.dev")

# Without a key, predictions use the offline word list.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Single switch (Arduino + EMG sensor) on a serial port, e.g. COM3. Empty = no hardware (spacebar only).
SWITCH_SERIAL_PORT = os.environ.get("SWITCH_SERIAL_PORT", "").strip()
try:
    SWITCH_BAUD = int(os.environ.get("SWITCH_BAUD", "9600"))
except ValueError:
    SWITCH_BAUD = 9600

# ONE reader for the whole server (a COM port can only be opened once); the hub copies
# each event to every open /ws/switch connection.
switch = SwitchBridge(SWITCH_SERIAL_PORT, SWITCH_BAUD)
switch_hub = SwitchHub()
switch.listeners.append(lambda event: switch_hub.publish(event))


@asynccontextmanager
async def lifespan(app):
    """Starts the switch reader with the server and stops it on shutdown (frees the COM port)."""
    if switch.enabled:
        switch.start()
    else:
        print("[switch] SWITCH_SERIAL_PORT not set: scanning works with the spacebar only")
    yield
    switch.stop()


app = FastAPI(lifespan=lifespan)


def call_colab(method: str, path: str, **kwargs):
    """Sends a request to Colab through ngrok.
    Retries once only on a dropped connection; an error reply or timeout would just repeat slow GPU work."""
    send = requests.post if method == "post" else requests.get
    url = f"{COLAB_API_URL.rstrip('/')}{path}"
    headers = {"ngrok-skip-browser-warning": "true"}   # skips ngrok's free-tier warning page
    try:
        return send(url, headers=headers, **kwargs)
    except requests.exceptions.ConnectionError as e:   # SSL errors are connection errors too
        print(f"[colab] {path}: connection problem ({type(e).__name__}), retrying once...")
        time.sleep(1)
        return send(url, headers=headers, **kwargs)


# Page files are sent with no-cache so the browser never runs a stale app.js.
API_PATHS = ("/predict", "/speak", "/upload_voice_clip", "/voice_bank",
             "/profiles", "/stock_speakers", "/guided", "/stats", "/uploads", "/switch", "/ws", "/indic")


@app.middleware("http")
async def no_cache_for_frontend(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith(API_PATHS):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


def call_gemini(prompt: str, max_tokens: int = 200, timeout: float = 8, temperature: float = 0.4) -> str:
    """Sends the prompt to Gemini and returns the raw text reply."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    r = requests.post(
        url,
        json=body,
        headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def prepare_text_for_speech(text: str, emotion: str = "neutral") -> str:
    """Converts ALL CAPS to sentence case and adds end punctuation (XTTS reads caps badly)."""
    text = " ".join(text.split())
    if not text:
        return text

    # only lowercase mostly-caps text, so properly typed text is kept
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        text = text.lower()

    text = text[0].upper() + text[1:]
    text = re.sub(r"\bi\b", "I", text)

    # punctuation shapes the delivery, so it follows the emotion
    if text[-1] not in ".!?":
        text += "!" if emotion in ("urgent", "happy") else "."
    return text


# Endpoints that wait for Colab or Gemini are plain `def`: FastAPI runs them on a worker
# thread, so a long generation doesn't block other requests such as predictions.
def gemini_key_is_set() -> bool:
    return bool(GEMINI_API_KEY) and not GEMINI_API_KEY.startswith("PASTE_")


@app.post("/predict")
def predict(text: str = Form("")):
    """Suggests 3 likely sentences for the text so far."""
    result = get_suggestions(text, call_gemini=call_gemini if gemini_key_is_set() else None)
    return result


def convert_with_gemini(prompt: str) -> str:
    """Gemini for text conversion: more room for Indian scripts, and no creativity."""
    return call_gemini(prompt, max_tokens=1024, timeout=20, temperature=0)


@app.get("/indic/languages")
async def indic_languages():
    """IndicF5's languages for the page (names, not codes), and whether Gemini can convert text."""
    return {"languages": language_list(), "modes": list(TEXT_MODES), "gemini": gemini_key_is_set()}


# Data folder can be moved with REVOICE_DATA_DIR; default is backend/data.
DATA_DIR = os.environ.get("REVOICE_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
store = ProfileStore(DATA_DIR)
GENERATION_LOG = os.path.join(DATA_DIR, "generation_log.csv")
AUDIT_LOG = os.path.join(DATA_DIR, "audit_log.csv")      # who opened/changed recordings (caregiver_pin.py)

# Used only when Colab is offline; the live list comes from Colab's /speakers.
FALLBACK_STOCK_SPEAKERS = [
    "Ana Florence", "Claribel Dervla", "Daisy Studious", "Gracie Wise",
    "Andrew Chipper", "Craig Gutsy", "Damien Black", "Viktor Eka",
]


@app.exception_handler(ProfileError)
async def profile_error(request: Request, exc: ProfileError):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(ProfileNotFound)
async def profile_not_found(request: Request, exc: ProfileNotFound):
    return JSONResponse({"error": str(exc)}, status_code=404)


def resolve_profile(profile_id: str = "") -> dict:
    """The named profile, or else the selected one."""
    profile_id = profile_id or store.active_id()
    if not profile_id:
        raise ProfileError("No user selected. Click '+ Add user' first.")
    return store.get(profile_id)


# Caregiver PIN (caregiver_pin.py). Needed for: listing, playing, downloading, uploading and
# deleting recordings, guided banking, dataset export, deleting a user, the audit log.
# NEVER for /speak, /predict or scanning: the patient can always talk.
guard = PinGuard()


@app.exception_handler(AccessDenied)
async def access_denied(request: Request, exc: AccessDenied):
    return JSONResponse(exc.body(), status_code=exc.status)


def require_unlocked(profile: dict, token: str, action: str):
    """Lets a protected action through only with a valid session token for this user.
    Every attempt, allowed or not, goes in the audit log."""
    pid = profile["id"]
    if not profile["pin_set"]:
        audit(AUDIT_LOG, pid, action, False, "no PIN set yet")
        raise AccessDenied(403, "pin_not_set", f"Set a caregiver PIN for {profile['name']} first: "
                                               "it protects their voice recordings.", profile_id=pid)
    if not guard.use(token, pid):
        audit(AUDIT_LOG, pid, action, False, "locked")
        raise AccessDenied(401, "locked", f"Locked: enter {profile['name']}'s caregiver PIN.", profile_id=pid)
    audit(AUDIT_LOG, pid, action, True)


def session_reply(profile_id: str) -> dict:
    """A new session token for this user (the page keeps it in memory only)."""
    return {"token": guard.issue(profile_id), "token_seconds": TOKEN_IDLE_S}


# PBKDF2 takes ~0.1 s, so these are plain `def` (worker thread), like the Colab/Gemini endpoints.
@app.post("/profiles/{profile_id}/unlock")
def unlock_profile(profile_id: str, pin: str = Form(...)):
    """Correct PIN -> session token. 5 wrong in a row -> locked out for 5 minutes."""
    profile = store.get(profile_id)
    record = store.pin_record(profile_id)
    if not record:
        raise AccessDenied(403, "pin_not_set", f"{profile['name']} has no caregiver PIN yet: set one.",
                           profile_id=profile_id)
    try:
        guard.check_pin(profile_id, record, pin)
    except AccessDenied as e:
        audit(AUDIT_LOG, profile_id, "unlock", False, e.code)
        raise
    audit(AUDIT_LOG, profile_id, "unlock", True)
    return session_reply(profile_id)


@app.post("/profiles/{profile_id}/lock")
async def lock_profile(profile_id: str):
    """"Lock now": ends every session of this user (needs no PIN: locking is always safe)."""
    store.get(profile_id)
    guard.lock_profile(profile_id)
    audit(AUDIT_LOG, profile_id, "lock", True)
    return {"locked": True}


@app.post("/profiles/{profile_id}/pin")
def set_pin(profile_id: str, new_pin: str = Form(...), old_pin: str = Form("")):
    """Sets the PIN (first time: no old PIN) or changes it (the old PIN is required and counts
    towards the lockout). Old sessions end; a new token is returned."""
    store.get(profile_id)
    check_new_pin(new_pin)
    record = store.pin_record(profile_id)
    action = "change_pin" if record else "set_pin"
    if record:
        try:
            guard.check_pin(profile_id, record, old_pin)
        except AccessDenied as e:
            audit(AUDIT_LOG, profile_id, action, False, e.code)
            raise
    store.set_pin_record(profile_id, hash_pin(new_pin))
    guard.lock_profile(profile_id)
    audit(AUDIT_LOG, profile_id, action, True)
    return session_reply(profile_id)


@app.get("/profiles/{profile_id}/lock_status")
async def lock_status(profile_id: str, token: str = Header("", alias=TOKEN_HEADER)):
    """For the lock indicator: PIN set? unlocked (and for how long)? locked out?"""
    profile = store.get(profile_id)
    return {"pin_set": profile["pin_set"], "seconds_left": guard.seconds_left(token, profile_id),
            "locked_out_seconds": guard.locked_out_for(profile_id)}


@app.get("/profiles/{profile_id}/audit")
async def audit_log(profile_id: str, token: str = Header("", alias=TOKEN_HEADER)):
    """This user's audit log, newest first (behind the PIN; holds no PIN or audio)."""
    require_unlocked(store.get(profile_id), token, "view_audit_log")
    return {"rows": read_audit(AUDIT_LOG, profile_id)}


@app.get("/profiles")
async def list_profiles():
    """All users, plus which one is selected."""
    return {"profiles": store.list(), "active": store.active_id()}


@app.post("/profiles")
def create_profile(
    name: str = Form(...),
    voice_type: str = Form("cloned"),
    stock_speaker: str = Form(""),
    consent_given: bool = Form(False),
    pin: str = Form(""),
):
    """Adds a user and selects them. With a caregiver PIN, the reply also has a session token,
    so the caregiver can start recording at once."""
    if pin:
        check_new_pin(pin)                     # before anything is created
    profile = store.create(name, voice_type, stock_speaker, consent_given)
    store.select(profile["id"])
    print(f"[profiles] created {profile['id']} ({voice_type})")
    if not pin:
        return store.get(profile["id"])
    store.set_pin_record(profile["id"], hash_pin(pin))
    audit(AUDIT_LOG, profile["id"], "set_pin", True, "new user")
    return {**store.get(profile["id"]), **session_reply(profile["id"])}


@app.post("/profiles/{profile_id}/select")
async def select_profile(profile_id: str):
    return store.select(profile_id)


@app.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str, token: str = Header("", alias=TOKEN_HEADER)):
    """Deletes a user and all their recordings, and asks Colab to forget cached voices (best effort)."""
    require_unlocked(store.get(profile_id), token, "delete_profile")
    store.delete(profile_id)
    guard.forget_profile(profile_id)
    print(f"[profiles] deleted {profile_id}")
    try:
        r = call_colab("post", "/forget_voices", timeout=10)
        colab_cleared = r.status_code == 200
    except requests.exceptions.RequestException:
        colab_cleared = False
    return {"deleted": profile_id, "active": store.active_id(), "colab_cache_cleared": colab_cleared}


@app.get("/stock_speakers")
def stock_speakers():
    """Built-in XTTS voices, for patients with no healthy voice left to bank."""
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
                            profile_id: str = Form(""), token: str = Header("", alias=TOKEN_HEADER)):
    """Saves a recorded voice clip for one emotion."""
    profile = resolve_profile(profile_id)
    require_unlocked(profile, token, "record_clip")          # the PIN check comes first
    if file is None:
        raise ProfileError("No audio file was sent.")
    extension = os.path.splitext(file.filename or "")[1] or ".webm"
    count = store.add_clip(profile["id"], emotion, await file.read(), extension)
    return {"status": "saved", "profile": profile["id"], "emotion": emotion, "clips": count}


@app.get("/voice_bank")
async def voice_bank_status(profile_id: str = ""):
    """Number of clips per emotion."""
    if not (profile_id or store.active_id()):
        return {}
    return resolve_profile(profile_id)["clip_counts"]


# Guided voice banking: one clip per sentence, saved with its exact words.
@app.get("/guided/sentences")
async def guided_sentences():
    return {"sentences": [{"id": sid, "text": text} for sid, text in GUIDED_SENTENCES],
            "session_size": SESSION_SIZE}


@app.get("/guided/status")
async def guided_status(profile_id: str = "", token: str = Header("", alias=TOKEN_HEADER)):
    """Progress, total length, and the next sentence to record."""
    profile = resolve_profile(profile_id)
    require_unlocked(profile, token, "guided_status")
    return store.guided_status(profile["id"])


@app.post("/guided/record")
async def guided_record(sentence_id: str = Form(...), duration_s: float = Form(...),
                        file: UploadFile = None, profile_id: str = Form(""), token: str = Header("", alias=TOKEN_HEADER)):
    """Saves one guided recording; recording a sentence again replaces it."""
    profile = resolve_profile(profile_id)
    require_unlocked(profile, token, "guided_record")          # the PIN check comes first
    if file is None:
        raise ProfileError("No audio file was sent.")
    extension = os.path.splitext(file.filename or "")[1] or ".webm"
    return store.save_guided(profile["id"], sentence_id, await file.read(), extension, duration_s)


@app.post("/guided/skip")
async def guided_skip(sentence_id: str = Form(...), profile_id: str = Form(""), token: str = Header("", alias=TOKEN_HEADER)):
    profile = resolve_profile(profile_id)
    require_unlocked(profile, token, "guided_skip")
    return store.skip_guided(profile["id"], sentence_id)


@app.get("/guided/audio/{sentence_id}")
async def guided_audio(sentence_id: str, profile_id: str = "", token: str = Header("", alias=TOKEN_HEADER)):
    """Plays back a saved guided recording."""
    profile = resolve_profile(profile_id)
    require_unlocked(profile, token, "play_recording")
    path = store.guided_audio_path(profile["id"], sentence_id)
    media = {".webm": "audio/webm", ".wav": "audio/wav", ".ogg": "audio/ogg",
             ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})


@app.post("/guided/references")
async def guided_use_as_references(profile_id: str = Form(""), token: str = Header("", alias=TOKEN_HEADER)):
    """Uses the best guided recordings as voice references."""
    profile = resolve_profile(profile_id)
    require_unlocked(profile, token, "guided_use_references")
    pid = profile["id"]
    store.pick_references(pid)
    return store.guided_status(pid)


@app.delete("/guided/references")
async def guided_clear_references(profile_id: str = "", token: str = Header("", alias=TOKEN_HEADER)):
    profile = resolve_profile(profile_id)
    require_unlocked(profile, token, "guided_clear_references")
    pid = profile["id"]
    store.clear_references(pid)
    return store.guided_status(pid)


# Uploaded old recordings: the browser decodes and trims them and sends a mono WAV.
@app.get("/uploads")
async def list_uploads(profile_id: str = "", token: str = Header("", alias=TOKEN_HEADER)):
    profile = resolve_profile(profile_id)
    require_unlocked(profile, token, "list_uploads")
    return store.list_uploads(profile["id"])


@app.post("/uploads")
async def upload_recording(file: UploadFile = None, profile_id: str = Form(""),
                           original_name: str = Form(""), start_s: float = Form(...),
                           end_s: float = Form(...), speech_s: float = Form(0.0),
                           consent: bool = Form(False), consented_by: str = Form(""), token: str = Header("", alias=TOKEN_HEADER)):
    """Saves one trimmed clip (validated in profiles.add_upload) and returns the updated list."""
    profile = resolve_profile(profile_id)
    require_unlocked(profile, token, "upload_recording")          # the PIN check comes first
    if file is None:
        raise ProfileError("No audio file was sent.")
    pid = profile["id"]
    data = await file.read(MAX_UPLOAD_BYTES + 1)     # never read more than the limit + 1 byte
    clip = store.add_upload(pid, data, original_name=original_name, start_s=start_s, end_s=end_s,
                            speech_s=speech_s, consent=consent, consented_by=consented_by)
    print(f"[uploads] {pid}: saved {clip['original_name']} ({clip['duration_s']}s), consent by {clip['consented_by']}")
    return {"saved": clip, **store.list_uploads(pid)}


@app.delete("/uploads/{clip_id}")
async def delete_uploaded_recording(clip_id: str, profile_id: str = "", token: str = Header("", alias=TOKEN_HEADER)):
    profile = resolve_profile(profile_id)
    require_unlocked(profile, token, "delete_upload")
    return store.delete_upload(profile["id"], clip_id)


@app.get("/profiles/{profile_id}/export")
async def export_dataset(profile_id: str, token: str = Header("", alias=TOKEN_HEADER)):
    """Downloads the user's guided recordings as an LJSpeech-style zip."""
    require_unlocked(store.get(profile_id), token, "export_dataset")
    data = build_dataset_zip(store, profile_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="revoice_dataset_{profile_id}.zip"'},
    )


INPUT_METHODS = ("pointer", "keyboard", "mixed", "scanning")     # only logged; speech is the same

# Engines the Colab notebook understands.
SUPPORTED_ENGINES = ("xtts", "f5", "indicf5")
# Engines that may get Indian-language text: IndicF5, and F5 only to compare against it.
INDIC_ENGINES = ("indicf5", "f5")
# Seconds to wait for Colab. IndicF5 loads on its first request and takes ~15-20 s per sentence.
COLAB_TIMEOUT = {"indicf5": 300}
DEFAULT_COLAB_TIMEOUT = 120


@app.post("/speak")
def speak(text: str = Form(...), emotion: str = Form(...), engine: str = Form("xtts"),
                profile_id: str = Form(""), selections: str = Form(""), utterance_id: str = Form(""),
                input_method: str = Form(""), language: str = Form("en"),
                text_mode: str = Form("transliterate"), converted_text: str = Form("")):
    """Generates speech and logs one row per call, successful or not.
    Typed and pointed text are spoken the same way; input_method is only logged.
    language other than "en" (IndicF5, or F5 to compare): the text is first converted to that
    language's script; converted_text (from an earlier reply) skips converting it again."""
    info = {}            # filled by generate_speech: voice_source, voice_reference, seconds, language...
    try:
        result = generate_speech(text, emotion, engine, profile_id, info,
                                 language=language, text_mode=text_mode, converted_text=converted_text)
        error = json.loads(result.body)["error"] if isinstance(result, JSONResponse) else ""
    except Exception as e:                # logged here, then raised to the normal error handler
        result, error = e, str(e)
    reply = result if isinstance(result, dict) else {}

    log_generation(
        GENERATION_LOG, text=text, engine=engine, emotion=emotion,
        voice_source=info.get("voice_source", ""), seconds=info.get("seconds"),
        success=not error, error=error,
        selections=int(selections) if selections.strip().isdigit() else None,
        utterance_id=utterance_id[:40],
        input_method=input_method if input_method in INPUT_METHODS else "",
        synth_seconds=reply.get("synth_seconds"), metrics=reply.get("metrics"),
        voice_reference=info.get("voice_reference"), ref_text=reply.get("ref_text"),
        language=info.get("language", "en"), text_mode=info.get("text_mode", ""),
        converted_text=info.get("converted_text", ""),
    )
    if isinstance(result, Exception):
        raise result
    return result


def generate_speech(text, emotion, engine, profile_id, info,
                    language="en", text_mode="transliterate", converted_text=""):
    """Finds the user's voice (recordings or stock voice) and asks Colab to speak the text."""
    if engine not in SUPPORTED_ENGINES:
        return JSONResponse(
            {"error": f"Unknown engine '{engine}'. Use one of: {', '.join(SUPPORTED_ENGINES)}."},
            status_code=400,
        )
    language = (language or "en").strip().lower()
    info["language"] = language
    if engine == "indicf5" and language == "en":
        return JSONResponse({"error": "IndicF5 speaks Indian languages: choose one (e.g. Hindi)."},
                            status_code=400)
    if language != "en":
        if language not in LANGUAGES:
            return JSONResponse({"error": f"Unknown language '{language}'. "
                                          f"Use one of: {', '.join(sorted(LANGUAGES))}."}, status_code=400)
        if engine not in INDIC_ENGINES:
            return JSONResponse({"error": "Indian languages work with IndicF5 (or F5-TTS, to compare)."},
                                status_code=400)

    profile = resolve_profile(profile_id)
    if language == "en":
        spoken_text = prepare_text_for_speech(text, emotion)
    else:
        # never send English to IndicF5 as if it were Hindi: convert first, or refuse
        if converted_text.strip() and in_target_script(converted_text, language):
            mode = text_mode if text_mode in (*TEXT_MODES, "as_typed") else "as_typed"
            converted = {"text": converted_text, "mode": mode}
        else:
            try:
                converted = convert_text(text, language, text_mode,
                                         call_gemini=convert_with_gemini if gemini_key_is_set() else None)
            except ConversionError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
        spoken_text = finish_indic_text(converted["text"], language, emotion)
        info.update(text_mode=converted["mode"], converted_text=spoken_text)
    payload = {
        "text": spoken_text,
        "language": language,
        "emotion": emotion,
        "engine": engine,
    }

    if profile["voice_type"] == "stock":
        if engine != "xtts":
            return JSONResponse(
                {"error": "Stock voices only work with XTTS-v2 (F5-TTS and IndicF5 need a recorded voice)."},
                status_code=400,
            )
        payload["stock_speaker"] = profile["stock_speaker"]
        info["voice_source"] = "stock"
        print(f"[speak:{engine}] {text!r} -> {spoken_text!r} with stock voice {profile['stock_speaker']!r}")
    else:
        # Follows the Voice reference setting; raises instead of silently using other clips.
        mode, source, refs = store.choose_references(profile["id"], emotion)
        refs = clips_for_engine(engine, refs)
        info["voice_source"] = source
        info["voice_reference"] = voice_reference_info(mode, source, refs, engine)
        clips = [base64.b64encode(r["data"]).decode("utf-8") for r in refs]
        # Same fields as Colab's Step 4b test, so the two scores are comparable.
        payload["stock_speaker"] = None
        payload["speaker_wav_base64"] = None
        payload["speaker_wav_base64_list"] = clips
        sent = ", ".join(f"{r['name']} ({r['duration_s'] if r['duration_s'] is not None else '?'}s)" for r in refs)
        print(f"[speak:{engine}] {text!r} -> {spoken_text!r} using {source} [{mode}]: {sent}")

    start = time.perf_counter()
    try:
        # the first generation on Colab can take 20-30 seconds (IndicF5's first: it loads too)
        response = call_colab("post", "/synthesize", json=payload,
                              timeout=COLAB_TIMEOUT.get(engine, DEFAULT_COLAB_TIMEOUT))
    except requests.exceptions.RequestException as e:
        info["seconds"] = time.perf_counter() - start
        return JSONResponse({"error": f"Could not reach Colab API: {e}"}, status_code=502)

    info["seconds"] = time.perf_counter() - start    # includes the network

    if response.status_code != 200:
        try:
            detail = response.json().get("error", response.text[:500])
        except ValueError:
            detail = response.text[:500]
        print(f"[Colab error {response.status_code}] {detail}")
        return JSONResponse(
            {"error": f"Colab said: {detail}"},
            status_code=502,
        )

    reply = response.json()
    if "voice_reference" in info:          # tell the page exactly which recordings were sent
        reply["voice_reference"] = info["voice_reference"]
    if language != "en":
        # the page shows the text in the language's script above the player
        reply.update(language=language, text_mode=info["text_mode"], converted_text=spoken_text)
        # the accent model only knows English accents, so its guess for this speech means nothing
        output = (reply.get("metrics") or {}).get("output")
        if output:
            output["accent_scores"] = None
            reply["metrics"]["accent_match"] = None
    return reply


def clips_for_engine(engine, refs):
    """F5 and IndicF5 copy one clip, so they get only that one (Colab measures similarity against every
    clip sent); XTTS uses all clips. The clip: first in order, or the longest recorded emotion clip."""
    if engine not in ("f5", "indicf5") or len(refs) <= 1:
        return refs
    if refs[0]["source"] == "emotion_clips":
        return [max(refs, key=lambda r: r["duration_s"] or 0)]      # ties: the oldest
    return refs[:1]


def voice_reference_info(mode, source, refs, engine="") -> dict:
    """Which recordings were sent, for the page and the log."""
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
    """Sets which recordings to copy the voice from; with "uploads", clip_id picks one clip."""
    return store.set_voice_reference(profile_id, mode, clip_id)


@app.get("/voice_bank/references")
async def preview_references(emotion: str = "neutral", profile_id: str = "", engine: str = "f5"):
    """Which recordings the next /speak would send; {"error": ...} if the setting can't be met."""
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
    """Generation time per engine and selections saved, from the log."""
    return summarize(GENERATION_LOG)


# Single switch for scanning mode.
@app.websocket("/ws/switch")
async def switch_socket(ws: WebSocket):
    """Sends {"state": "DOWN"|"UP", "t": time} to this page for every switch event."""
    queue = switch_hub.subscribe()        # before accept(), so no event right after connecting is lost
    sender = None

    async def send_events():
        while True:
            await ws.send_json(await queue.get())

    try:
        await ws.accept()
        sender = asyncio.create_task(send_events())
        while True:                       # the page sends nothing; this just notices when it closes
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if sender:
            sender.cancel()
        switch_hub.unsubscribe(queue)


@app.get("/switch/status")
async def switch_status():
    """enabled, connected, port, last_event... for the "Switch: connected" pill."""
    return {**switch.status(), "clients": switch_hub.client_count()}


class SimulatedSwitch(BaseModel):
    state: str


@app.post("/switch/simulate")
async def switch_simulate(body: SimulatedSwitch):
    """A fake switch event (tests and demos without hardware); debounced like the real one."""
    state = body.state.strip().upper()
    if state not in VALID_STATES:
        return JSONResponse({"error": f"state must be one of {', '.join(VALID_STATES)}"}, status_code=400)
    event = switch.handle_state(state)
    return {"accepted": event is not None, "event": event}


# Mounted last so it doesn't shadow the API routes.
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
