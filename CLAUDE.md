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
  - Run tests:   `cd backend; py -3.10 -m pytest`   and   `node --test "frontend/tests/*.test.mjs"`
    (Node 24 needs the quoted file pattern, not a folder)
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
            audio_prep.js PURE: mixToMono, trimSamples, resample (Kaiser windowed-sinc, ~90 dB
                         anti-alias), convertForUpload (mono->trim->resample->WAV, counts
                         clipping), encodeWav16 (rounded), readWav16, waveformPeaks,
                         speechChunks/speechSecondsInRange, allowed upload extensions
            metrics_format.js PURE: metrics -> lines under players; moreSimilarEngine();
                         referenceLines(voice_reference) -> "Voice copied from..." lines
            selection_stats.js PURE: selections vs letter-by-letter (letters + single
                         spaces), same rule as generation_log.py; typed=true -> no savedPct
            compose.js   PURE: the ONE sentence {text, selections, typed}: pointerKey (append,
                         +1), choosePrediction (replace, +1), typed (exact text, not a
                         selection), textBoxKeyAction (Ctrl/Cmd+Enter speak, Enter ignored),
                         isTypingTarget (page shortcuts off in input/select/textarea/
                         contenteditable), inputMethod pointer|keyboard|mixed. #composedText
                         is a <textarea>; app.js changes text ONLY via setSentence(). Typed
                         sentences send no `selections` (excluded from savings), log
                         input_method.
            audio_checks.js PURE: analyzeLevels() flags too_quiet / clipping using the
                         90th-percentile 10 ms chunk RMS (a start click can't hide a quiet take)
            indic.js     PURE: IndicF5 choices (indicf5 | compare_indic -> ["indicf5","f5"]),
                         previewEngine, generatingMessage ("Generating in Hindi… about 15 s",
                         +5 s per 40 chars over 80), convertedLabel. app.js: language select
                         from GET /indic/languages + "typed text is" radios (localStorage
                         revoice.indic), #convertedBox above the player; compare sends F5 the
                         converted_text IndicF5 spoke; scan.progressText shows while scanning waits.
            scanning.js PURE: single-switch row-column Scanner (time passed in: start/tick/
                         press/setPaused/setRows/view), buildScanRows (row 0 = 3 predictions,
                         letters by frequency 7/row, controls SPACE/DEL/SPEAK/CLEAR),
                         applyScanSelection -> compose.js pointerKey/choosePrediction (same
                         counting as the pointer), normalizeScanSettings, switchPillText.
                         app.js: Input "scanning" hides camera (loop skips tracking), handleSelect()
                         from Space or /ws/switch DOWN, SPEAK = speak() + pause until audio ends.
                         Space: document keydown/keyup CAPTURE + preventDefault (scanSpaceAction:
                         works on any focused control, not in text boxes); grid tabindex=-1 gets
                         focus on entering scanning; window blur -> "Click here to resume scanning".
            tests/       node:test tests for the pure JS modules
backend/    app.py       FastAPI: GET/POST /profiles, POST /profiles/{id}/select,
                         DELETE /profiles/{id} (also calls Colab /forget_voices),
                         GET /stock_speakers, POST /upload_voice_clip, GET /voice_bank,
                         POST /speak (text, emotion, engine, profile_id -> Colab /synthesize),
                         POST /predict (Gemini). Endpoints use the selected profile unless
                         profile_id is given. prepare_text_for_speech() fixes ALL CAPS and
                         adds emotion punctuation. No-cache middleware for frontend files
                         (add new API prefixes to API_PATHS). StaticFiles mounted LAST.
                         Endpoints that call Colab/Gemini MUST be plain `def` (worker thread),
                         never `async def` — blocking calls there freeze the whole server
                         (tests/test_no_freeze.py guards this).
            profiles.py  ProfileStore: data/profiles/<id>/profile.json + clips/<emotion>/
                         000001.webm... (newest 3 kept). data/settings.json = selected user.
                         Ids are validated (SAFE_ID) because they become folder names.
                         guided/<gNNN>.webm + guided/manifest.json (exact text, duration,
                         skipped, references). /speak uses guided references when an
                         emotion has no clips. pick_references = longest takes to ~30 s
                         (XTTS concatenates refs and uses the first gpt_cond_len=30 s).
            uploads (profiles.py): data/profiles/<id>/uploads/NNNNNN.wav + manifest.json
                         (original name, trim, duration, speech_s, consented_by/at + exact
                         statement). Browser decodes (decodeAudioData), then audio_prep.js
                         convertForUpload (NOT OfflineAudioContext: untestable) posts mono
                         16-bit 24 kHz WAV (tests/upload_conversion.test.mjs); server checks
                         consent, extension, 8 MB, WAV format, trim 1-120 s matching length.
                         Voice reference per profile (profile.json voice_reference): auto
                         (emotion clips > uploads > guided) | uploads | recorded | guided.
                         choose_references() -> (mode, source, refs[{source,name,duration_s,
                         data}]); a forced mode with nothing raises ProfileError, NEVER a
                         silent fallback (that bug sent mic clips instead of the upload:
                         similarity 0.46 vs 0.80). webm_duration() reads MediaRecorder WebM
                         length from the last block time stamp (no ffmpeg).
                         /speak replies + logs voice_reference {mode, source, clips, total};
                         GET /voice_bank/references?engine= previews it; POST
                         /profiles/{id}/voice_reference (mode, clip_id) sets it.
                         profile.reference_clip = one upload id ("" = all); deleted -> error.
                         app.clips_for_engine: F5 gets ONE clip (uploads/guided: first in
                         order; emotion clips: longest) so Colab's similarity is measured on
                         the clip F5 copied; XTTS gets all. Payload = Step 4b's fields exactly
                         (stock_speaker None, speaker_wav_base64 None, list of clips).
                         app.js TEST_SENTENCE == notebook Step 4b == parity_check (test enforces).
            guided_sentences.py  350 sentences: g001-g150 short phrases, g151-g350 longer
                         (12-20 words, no names/digits/abbreviations; tests enforce it).
                         ~26 min of speech. NEVER renumber; append only. SESSION_SIZE=50.
            generation_log.py    one CSV row per /speak call in data/generation_log.csv
                         (no text stored); summarize() -> /stats/summary and report.py.
                         Savings counted once per utterance_id (latest row).
                         Also ref_mode, ref_files ("name 18.0s | ..."), ref_seconds, ref_text.
            parity_check.py      FILE [--profile ID] [--runs N]: same bytes via the Step 4b
                         request vs generate_speech() (throwaway user in a temp folder), prints
                         both similarities + md5 of what was sent. F5 varies about +-0.03 per run.
            accent.py    judge_accent (>=3 s speech, else too_short; confidence from top1-top2
                         margin: <0.05 low, <0.15 medium, else high), compare_accents (only
                         when both sides ok AND >= medium). SAME rules in metrics_format.js
                         (judgeAccent/compareAccents/accentLines). Log: ref/out_accent_scores.
                         Page hides accents unless "Show accent estimates" (localStorage).
            dataset_export.py    LJSpeech zip: metadata.csv `id|text|text`, no header, "
"
                         endings; wavs/ (22050 mono) if ffmpeg on PATH, else raw/ webm
                         (ffmpeg is NOT installed on this Windows machine).
            switch_bridge.py  SwitchBridge: ONE reader thread for SWITCH_SERIAL_PORT (FastAPI
                         lifespan start/stop), only "DOWN"/"UP", 300 ms DOWN debounce, missing/
                         busy/unplugged -> warn once, retry 3 s. SwitchHub -> every /ws/switch
                         client via call_soon_threadsafe. GET /switch/status, POST
                         /switch/simulate {"state"} (debounced too). Tests use a fake port.
            tools/       twitch_to_space.py (EMG DOWN -> pyautogui Space, 0.4 s lockout, --fake),
                         emg_calibrate.py (raw-stream sketch, replays switch_emg.ino maths,
                         writes THRESHOLD only if the replay is perfect), serial_tools.py
                         (port detect, busy/missing messages, arduino-cli at C:\Program Files                         Arduino CLI; sketch staged into <name>/<name>.ino). Option A
                         (twitch_to_space, SWITCH_SERIAL_PORT empty) XOR option B (bridge).
                         conftest refuses real serial.Serial. /speak input_method also "scanning".
            indic_languages.py  IndicF5's 11 languages (code -> name, script). SHARED BLOCK
                         (copied verbatim into Colab Step 3d; test enforces): count_syllables per
                         script (common ISCII layout of the 9 Unicode blocks; virama joins; final
                         'a' dropped in Devanagari/Bengali/Gurmukhi/Gujarati), 5 syl/s + pauses,
                         clamp 1-20 s, ratio fallback in CHARACTERS not bytes. convert_text():
                         already in script -> as_typed; else Gemini strict prompt (transliterate |
                         translate) and the reply must be in the script; no Gemini -> ConversionError
                         (English is never sent to IndicF5).
                         /speak: language, text_mode, converted_text (compare reuses it, no 2nd
                         Gemini call); indicf5 gets one clip like F5, 300 s timeout; reply adds
                         converted_text; output accent dropped for non-English. Log columns
                         language, text_mode, original_text, converted_text (text ONLY for
                         Indian-language rows). GET /indic/languages.
            predictor.py Gemini prompt, reply parsing, offline fallback, is_sensible()
            tests/       pytest; Colab + Gemini always mocked, real network calls fail;
                         each test gets a temporary data folder (never touches backend/data)
            data/        user data (gitignored). Override location with REVOICE_DATA_DIR.
colab/      revoice_server_v2.ipynb   THE ONLY VALID server notebook ("ReVoice API v9";
                                      /synthesize with clips or stock_speaker, /speakers,
                                      /forget_voices). Step 4a = voice functions
                                      (synthesize_core), shared by the Step 4b self-test and
                                      the Step 6 API. F5: up to 15s ref, F5's own
                                      preprocess_ref_audio_text trims + transcribes (so the
                                      transcript matches), speed 1.0, nfe_step 64, returns
                                      ref_text. Checked against f5-tts 1.1.22 source.
                                      v7: Step 3c loads SpeechBrain metric models
                                      (spkrec-ecapa-voxceleb, accent-id-commonaccent_ecapa);
                                      synthesize_core returns synth_seconds + metrics
                                      {similarity, reference/output {pitch_hz, pitch_range_hz,
                                      words_per_sec, speech_seconds, accent_scores}, accent_match};
                                      v8: accent_scores = top-3 [[label, RAW cosine -1..1]],
                                      NO x30+softmax percentages (unconfirmed scale, showed
                                      "US 100%"); accent_match stays None on Colab.
                                      every metric is None on failure. speechbrain 1.1.1 is
                                      installed --no-deps before the final pin line. Audio is
                                      loaded with librosa at 16 kHz (no torchaudio file IO).
                                      v9: Step 3d (after 3c) = IndicF5: downloads with the
                                      HF_TOKEN secret, loads on the FIRST request (lock), method B
                                      (CFM+DiT v0 built like load_model, float32, own vocoder
                                      weights). run_indicf5: F5's reference + English Whisper
                                      transcript (shared f5_cache), fix_duration =
                                      indicf5_total_seconds, nfe 32, peak -1 dBFS. generate()
                                      has an "indicf5" branch; health lists engines.
            indicf5_test.ipynb        IndicF5 (Hindi etc.) TEST only, server notebook untouched: runs
                                      Steps 1-4a unchanged, loads IndicF5 weights with our f5-tts 1.1.22
                                      (method B = direct safetensors load; method A AutoModel fails with
                                      a meta-tensor error), re-checks XTTS/F5/Whisper/SpeechBrain/versions.
                                      T7: garbled Hindi start = F5's duration guess counts UTF-8 BYTES
                                      (Devanagari 3/letter, English 1) -> ~3x too long; tries explicit
                                      duration, short ref, Devanagari ref_text, combos, nfe 32 vs 64.
            archive/                  old notebooks — never run them (they broke things)
```

## UI rules (redesign, tags pre-ui -> main)
- All colours are CSS variables on :root (light) + dark blocks for `[data-theme="dark"]`
  and `prefers-color-scheme: dark` (keep both dark blocks identical). The camera overlay
  reads `--canvas-*` via readOverlayColors() — only at start and on "revoice-themechange",
  never per frame.
- Animations: transform/opacity only; everything off under prefers-reduced-motion
  (`reducedMotion` in app.js for JS-driven ones). Never hide or delay error messages:
  toasts are extra, status text stays; error toasts don't auto-dismiss.
- Patient targets stay large; text contrast WCAG AA in both themes (checked by script).
- Loop cost baseline ~2.5 ms/frame (laser mode, fake camera); re-measure after overlay changes.

## Hard-won constraints (do NOT change)
- Colab runs Python 3.13 with torch 2.11 (Nov 2026: Steps 2b-4a + indicf5_test T1-T6 all passed
  with the pins below unchanged). coqui-tts 0.27.5 needs transformers >=4.57, but transformers 5.x
  removed `isin_mps_friendly`. Install order must stay:
  coqui-tts → f5-tts with `--no-deps` → its deps → ffmpeg → ONE final pinning line, LAST:
  `pip install -q tomli transformers_stream_generator boto3 bitsandbytes "transformers==4.57.6" "huggingface-hub>=0.34.0,<1.0" "tokenizers>=0.22.0,<=0.23.0" "numpy<2.3"`
  then `pip check` filtered to transformers|coqui|tts|f5|librosa|numba|torch|cached
  (diffusers/gradio conflicts are expected and ignored — unused), then Runtime → Restart session.
  **Known-working set:** transformers 4.57.6, huggingface-hub >=0.34.0,<1.0,
  tokenizers 0.22.0–0.23.0, numpy <2.3 (Step 2b checks all four).
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
- [x] 2 Guided voice banking (150 sentences, transcript per clip, LJSpeech zip export).
      Extended to 350 sentences (~26 min) in sessions of 50.
- [x] Report evidence (before Phase 3): generation log CSV, selection counter, report.py
- [x] Part A stability (Colab v6/v7 + metrics), F5 default — tag `review-safe` on main
- [ ] Part B upload old recordings — branch `upload-recordings` (merge after manual test)
- [ ] Part C light XTTS fine-tune
- [~] Final review Phase 1 IndicF5 (tag pre-indicf5): 1a safety test + T7 done; 1b-1e
      (Step 3d, backend, frontend, tests, docs) done, waiting for the Colab manual test
- [ ] 3 Fine-tune XTTS (colab/finetune_xtts.ipynb, checkpoints to Drive, engine "xtts_ft")
- [ ] 4 Safety layer: Emergency Override, False Selection Guard, Ambiguous Phrase Check,
      Caregiver Validation (Confirm / Escalate / Override, toggleable)
- [~] 5 Scanning mode + single switch: DONE spacebar + Arduino EMG via backend serial bridge
      (hardware/switch_emg.ino, tag `scanning-working`). Not done: blink/eyebrow, TCRT5000.
- [ ] 6 Evaluation (generation CSV log, blind listening test, summary per engine)
