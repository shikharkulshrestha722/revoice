# ReVoice — notes for Claude

GenAI college course project. Assistive communication for people with paralysis/ALS:
the patient selects letters by pointing (finger, laser, later a single switch), GenAI
predicts the full phrase, and it is spoken in the patient's OWN cloned voice with the
right emotional tone.

## Golden rules
- **Every control the PATIENT uses must work with the pointer/switch**, never only with
  a mouse. Caregiver-only screens (setup, profiles, validation) may use a mouse.
- Don't rewrite working parts. Small, focused git commits with clear messages.
- Comment code in simple language — the author has to explain it to a teacher.
- Never commit secrets or hardcode keys. Keys live in `backend/.env` (gitignored).
- If unsure how a library works in the installed version, read the installed source.
- Work one phase at a time (see below). At the end of each phase: run tests, summarise,
  list manual tests (Colab cells, recording, camera), then STOP and wait for "continue".
- If a phase is bigger than expected, say so before going deep.

## Environment
- Windows, PowerShell. Project root: `C:\Users\SHIKHAR\OneDrive\Desktop\revoice`
- **Always use Python 3.10 (`py -3.10`) for BOTH the backend and the tests**, so they run
  on the same interpreter. (Plain `python` on PATH is 3.11 and has no FastAPI.)
  - Run backend: `cd backend; py -3.10 -m uvicorn app:app --reload` → http://localhost:8000
  - Run tests:   `cd backend; py -3.10 -m pytest`
  - Install deps: `py -3.10 -m pip install -r backend/requirements.txt`
- Settings: copy `backend/.env.example` to `backend/.env` (COLAB_API_URL, GEMINI_API_KEY).
- Voice models run on Google Colab (free T4) via ngrok. Static ngrok domain:
  https://handcart-retrieval-mulch.ngrok-free.dev
- Claude cannot run Colab cells: update the notebook and tell the user exactly which
  cells to run.

## Structure
```
frontend/   index.html, style.css
            app.js       ES module: camera, MediaPipe hands (dynamic import), laser mode,
                         5x6 letter grid with dwell, 3-slot prediction bar at top of canvas
                         (cell ids 1000+), 20s voice recording (browser echo/noise/gain
                         processing DISABLED on purpose), engine select xtts | f5 | both
                         ("both" runs sequentially — one shared GPU — and shows timings)
            tracking.js  PURE logic, no DOM (keep it that way so it stays testable):
                         OneEuroFilter, PointerTracker, detectLaserBlob, DwellSelector
backend/    app.py       FastAPI: GET/POST /profiles, POST /profiles/{id}/select,
                         DELETE /profiles/{id} (also calls Colab /forget_voices),
                         GET /stock_speakers, POST /upload_voice_clip, GET /voice_bank,
                         POST /speak (text, emotion, engine, profile_id -> Colab /synthesize),
                         POST /predict (Gemini). Endpoints use the selected profile unless
                         profile_id is given. prepare_text_for_speech() fixes ALL CAPS and
                         adds emotion punctuation. No-cache middleware for frontend files
                         (add new API prefixes to API_PATHS). StaticFiles mounted LAST.
            profiles.py  ProfileStore: data/profiles/<id>/profile.json + clips/<emotion>/
                         000001.webm... (newest 3 kept). data/settings.json = selected user.
                         Ids are validated (SAFE_ID) because they become folder names.
            predictor.py Gemini prompt, reply parsing, offline fallback, is_sensible()
            tests/       pytest; Colab + Gemini always mocked, real network calls fail;
                         each test gets a temporary data folder (never touches backend/data)
            data/        user data (gitignored). Override location with REVOICE_DATA_DIR.
colab/      revoice_server_v2.ipynb   THE ONLY VALID server notebook ("ReVoice API v5":
                                      /synthesize with clips or stock_speaker, /speakers,
                                      /forget_voices). Only Step 6 holds the API code.
            archive/                  old notebooks — never run them (they broke things)
```

## Hard-won constraints (do NOT change)
- Colab runs Python 3.13. coqui-tts 0.27.5 needs transformers >=4.57, but transformers 5.x
  removed `isin_mps_friendly`. Install order must stay:
  coqui-tts → f5-tts with `--no-deps` → its deps → LAST:
  `pip install --no-deps --force-reinstall "transformers>=4.57,<5"` and `"numpy<2.3"`,
  then Runtime → Restart session.
  **Never use a plain `--force-reinstall`** (it upgraded numpy and broke librosa/numba).
- Set `COQUI_TOS_AGREED=1` before loading XTTS.
- Browser audio is webm/opus; Colab converts it with ffmpeg. Keep that.
- Send header `ngrok-skip-browser-warning: true` on every request to Colab.
- XTTS uses `get_conditioning_latents(gpt_cond_len=30)`; ffmpeg cleanup (highpass,
  silenceremove, loudnorm); F5 uses the longest clip trimmed to 11s + Whisper transcript.
- **F5-TTS weights are non-commercial licence** — note it in docs.

## Phases
- [x] 0 Foundation: git, .env, no-cache, tests, engine selector restored
- [x] 1 User profiles + persistent voice bank, delete, switcher, Add user panel,
      stock XTTS speakers (notebook v5)
- [ ] 2 Guided voice banking (~150 sentences, transcript per clip, LJSpeech zip export)
- [ ] 3 Fine-tune XTTS (colab/finetune_xtts.ipynb, checkpoints to Drive, engine "xtts_ft")
- [ ] 4 Safety layer: Emergency Override, False Selection Guard, Ambiguous Phrase Check,
      Caregiver Validation (Confirm / Escalate / Override, toggleable)
- [ ] 5 Scanning mode + single switch (spacebar, long blink/eyebrow, Arduino Web Serial;
      hardware/switch_tcrt5000.ino, hardware/switch_emg.ino)
- [ ] 6 Evaluation (generation CSV log, blind listening test, summary per engine)
