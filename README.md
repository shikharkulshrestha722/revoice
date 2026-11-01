# ReVoice — Setup Guide

Assistive communication for people with paralysis/ALS: select letters by pointing,
GenAI predicts the phrase, and it is spoken in the patient's own cloned voice.

## What's in this folder

```
revoice/
├── frontend/       the webpage (webcam, letter grid, scanning keyboard, predictions, voice recorder)
├── backend/        the Python server connecting frontend <-> Colab and Gemini
│   ├── data/       users and their recordings (created automatically, not in git)
│   └── tests/      automated tests (Colab and Gemini are faked)
├── hardware/
│   └── switch_emg.ino   Arduino sketch: EMG muscle sensor -> single switch ("DOWN"/"UP")
└── colab/
    ├── revoice_server_v2.ipynb   the voice server (XTTS-v2 + F5-TTS + IndicF5) — the ONLY one to use
    ├── indicf5_test.ipynb        test only: Hindi (IndicF5) next to XTTS/F5 without breaking them
    └── archive/                  old notebooks — do NOT run these
```

> **Only `colab/revoice_server_v2.ipynb` is valid.** Running cells from older notebooks
> in the same Colab session breaks the installed packages and steals the ngrok URL.

## How to run it (in order)

### 1. Start the Colab notebook
1. Upload `colab/revoice_server_v2.ipynb` to https://colab.research.google.com
2. **Runtime → Change runtime type → T4 GPU**
3. Run Step 1 and Step 2 (install). Then **Runtime → Restart session**.
4. Run Step 2b (version check — it stops with a clear message if a version is wrong),
   Step 3, Step 3b (also downloads Whisper), Step 4 and **Step 4a** (voice functions).
5. Optional: **Step 4b** — upload a recording and hear XTTS/F5 clone it with the exact
   API code. Gibberish here too = install problem; fine here but bad in the app = the
   app's reference clips are the problem.
   Step 3c loads two small models that only MEASURE results (speaker similarity and an
   English accent estimate).
   Optional: **Step 3d** sets up IndicF5 for Indian languages (see
   [Indian languages (IndicF5)](#indian-languages-indicf5); needs a one-time Hugging Face token).
6. Paste your ngrok token (https://dashboard.ngrok.com/get-started/your-authtoken) in Step 5.
7. Run Step 6. The last line must say **ReVoice API v9** and lists the loaded engines.

Last checked on Colab with **Python 3.13 and torch 2.11** (November 2026): the install and
version checks pass with the pinned versions unchanged.

Our free ngrok domain is static, so the URL stays the same every session:
`https://handcart-retrieval-mulch.ngrok-free.dev`

### 2. Configure the backend (first time only)
Copy `backend/.env.example` to `backend/.env` and fill in:
```
COLAB_API_URL=https://handcart-retrieval-mulch.ngrok-free.dev
GEMINI_API_KEY=your-key-from-aistudio.google.com
```
`.env` is ignored by git, so keys never get committed. Without a Gemini key, predictions
still work from an offline word list.

Optional, only for the switch's **Option B** (see "Finger-twitch switch" below; leave it
empty for Option A):
```
SWITCH_SERIAL_PORT=COM3
SWITCH_BAUD=9600
```

### 3. Install and run the backend (always Python 3.10)
```powershell
cd backend
py -3.10 -m pip install -r requirements.txt
py -3.10 -m uvicorn app:app --reload
```

### 4. Open the app
Go to **http://localhost:8000** in Chrome.

### Running the tests
```powershell
cd backend
py -3.10 -m pytest
cd ..
node --test "frontend/tests/*.test.mjs"
```

## How to use it

1. **Pick a user** at the top, or click **+ Add user**:
   - *Record my voice*: the person's voice is cloned. They must tick the consent box.
   - *Use a stock voice*: for someone with no healthy voice left to record. Pick one of
     XTTS's built-in voices (works with XTTS-v2 only).
   - *Use old recordings*: for someone who can no longer record — see "Upload old
     recordings" below. A family member or legal guardian gives consent.
   - **Delete user** removes the user and all their recordings from disk, and tells Colab
     to forget the voice. (Use this after cloning a volunteer's voice in a demo.)
2. Click **Start Camera** and allow webcam access.
3. Choose **Finger** or **Laser** tracking. Hold the pointer still on a letter until the
   green ring fills (~1.5 s) to select it. The top row shows 3 AI-predicted phrases —
   dwell on one to use it.
4. Pick an emotion, click **Record Voice Clip**, and speak naturally in that tone
   (6–20 s; click again to stop early). 2–3 clips per emotion works best.
5. **Guided voice banking** (optional, for a better voice): click *Guided voice banking*
   and read the sentences one at a time. There are 350 (about 25-30 minutes), split into
   7 sessions of 50 so you can spread them over several sittings (<kbd>Space</kbd> record/stop, <kbd>→</kbd> skip,
   <kbd>←</kbd> back, <kbd>P</kbd> play). It remembers where you stopped. Too-quiet or
   distorted takes are flagged so you can redo them.
   - *Use my best recordings as voice references*: speech then works for every emotion
     even without separate emotion clips.
   - *Download dataset (.zip)*: an LJSpeech-style training set (metadata.csv + audio) for
     fine-tuning XTTS on this voice. Without ffmpeg installed, the zip holds the original
     .webm clips and the Colab fine-tuning notebook converts them.
6. Pick a voice engine — **F5-TTS** (default), **XTTS-v2**, or **Compare both** (generates with
   each, one after the other, and shows how long each took) — then **Generate & Speak**.
   For Hindi, Tamil and 9 more languages pick **IndicF5** (next section).
7. **Typing** (caregivers, testing, or a user who can still type): the composed sentence is
   also a text box. Typing and pointing edit the SAME sentence: typing changes it, pointer
   letters are added at the end, a prediction (on the camera overlay, or as a button under
   the box) replaces it, and **Clear** empties it. Predictions update as you type.
   <kbd>Ctrl</kbd>+<kbd>Enter</kbd> speaks; plain <kbd>Enter</kbd> does nothing. While the
   box has focus, page shortcuts (like <kbd>Space</kbd> to record in guided banking) are off.
   Typed text is spoken exactly like pointed text (same clean-up, emotion, engine and voice
   reference). Typed characters aren't pointer selections, so a typed sentence shows its
   pointer selections but no "saved by prediction" percentage, and the log marks it
   `keyboard`/`mixed` so it never counts in the savings summary.
8. **Scanning (single switch)**, for someone who can't point: set **Input** to
   *Scanning (single switch)* — see the next section.

## Indian languages (IndicF5)

**IndicF5** (AI4Bharat's F5-TTS fine-tune) speaks **Assamese, Bengali, Gujarati, Hindi,
Kannada, Malayalam, Marathi, Odia, Punjabi, Tamil and Telugu** in the person's own voice.
It is **cross-lingual**: an **English** recording is enough to speak Hindi in that voice,
which matters for someone who can no longer record anything new.

**One-time setup (IndicF5 is gated on Hugging Face):**
1. Log in at https://huggingface.co, open https://huggingface.co/ai4bharat/IndicF5 and
   **accept the terms**.
2. https://huggingface.co/settings/tokens → **Create new token** → type **Read** → copy it.
3. In Colab: **key icon** (left bar) → **Add new secret** → name `HF_TOKEN`, value = the
   token, switch **Notebook access** on.
4. Run **Step 3d** (downloads ~1.4 GB), then Steps 4a and 6 as usual. Step 6's reply shows
   `"indicf5": "ready (loads on the first request)"`. If 3d fails, XTTS and F5 still work.

**Using it (caregiver settings under Speak):**
- **Voice engine → IndicF5 (Indian languages)**, choose the **Language** (by name), and say
  what the typed text is:
  - *the language in English letters* → **transliterate** (`main paani chahta hoon` →
    `मैं पानी चाहता हूँ`)
  - *English, translate it* → **translate** (`I need water` → `मुझे पानी चाहिए`)
- Predictions stay in English; the patient composes and presses SPEAK exactly as before
  (pointer, scanning or switch). Gemini converts the sentence just before speaking and the
  page shows the result **above the player**, so the caregiver can check what was said.
- Text already typed in the language's script is spoken as typed (no Gemini needed).
  **Without a Gemini key, English is refused** with a clear message: it is never read out
  by IndicF5 as if it were Hindi.
- Each sentence takes **about 15–20 s** on the free T4 (the page says "Generating in Hindi…
  about 15 s"); the very first one also loads the model. Other requests (predictions,
  scanning, the switch) keep working meanwhile.
- **Compare IndicF5 vs F5** speaks the same converted sentence with both (plain F5 was not
  trained on Indian scripts, so this shows why IndicF5 is needed).
- The English accent estimate is not shown for Indian-language speech (the accent model
  only knows English accents); voice similarity, pitch and rate are.

**How the duration is set** (the fix found in `colab/indicf5_test.ipynb` Step T7): F5's own
length guess counts UTF-8 *bytes*; an Indian letter is 3 bytes and an English one 1, so with an
English reference the Hindi got ~3× too much time and started with 2–3 s of mumbling.
ReVoice instead counts **syllables** (vowel letters and vowel signs, per script) at about
5 per second plus short pauses at punctuation, kept between 1 and 20 s
(`backend/indic_languages.py`, copied into Step 3d). Measured on Hindi: no garbled start,
character error rate 0.00, similarity 0.78. Other languages use the same rule but were not
measured yet.

**Consent:** speaking a language the person never recorded is still their voice. Only use
it with the same consent as voice banking (the person, or their family/legal guardian), and
tell listeners that the speech is generated.

## Single switch: scanning mode

For a patient with **no pointing ability**: one switch is enough. The switch is a muscle
(EMG) sensor read by an Arduino, or the **spacebar** for testing.

**How it works.** Rows light up one at a time (bright yellow). Press the switch when the
row you want is lit; then the letters in that row light up one at a time — press again on
the letter. If you don't press during one pass of the letters, the rows start again, so a
wrong row is never a trap. After each selection there is a short "Get ready…" pause.

- **Row 1** = the 3 AI predictions (fastest way to a whole sentence).
- **Letters** are ordered by how common they are in English (E T A O I N S first), so
  common letters need less waiting.
- **Last row**: SPACE, ← DELETE, SPEAK, CLEAR. SPEAK uses the selected engine and emotion,
  and scanning pauses until the audio has finished playing.
- **Pause** button for the caregiver. **Scanning settings**: time per step (0.4–2.5 s,
  default 1.0 s), pause after each selection (default 1.5 s), optional soft click on each
  step. Saved in this browser.
- Selections are counted exactly like pointer selections (one per letter/prediction), so
  the "saved by prediction" figure works for scanning too.
- The pill at the top says **Switch: connected on COM3**, **not connected (COM3) — using
  spacebar** (hover it to see why), or **using spacebar** (no port set).

## Finger-twitch switch (Arduino + EMG sensor)

A small finger twitch is picked up by an EMG (muscle) sensor on the forearm. The Arduino
turns it into a `DOWN` line (and `UP` when the muscle relaxes) over USB.

**Wiring (sensor board → Arduino Uno):**

| Sensor pin | Arduino Uno |
|---|---|
| SIG | A0 |
| VCC | 5V |
| GND | GND |

**Pad placement (forearm, for a finger twitch):** palm side of the forearm, about a third of
the way from the elbow to the wrist (the finger-bending muscle — you feel it move when you
wiggle your fingers). Put the two **signal pads** (often marked MID and END) on that muscle,
2–3 cm apart, **in a line along the forearm**. Put the **reference pad** (REF) on bone with no
muscle under it: the bony point of the elbow, or the knob on the back of the wrist. Clean,
dry skin; press the pads on firmly; keep the cables from swinging.

**Tools** (Python 3.10, from the `backend` folder; arduino-cli: `winget install ArduinoSA.CLI`,
then `arduino-cli core install arduino:avr`):

| Tool | What it does |
|---|---|
| `py -3.10 tools/emg_calibrate.py` | Uploads a temporary sketch, asks you to relax 5 s and twitch 5 times, sets `THRESHOLD` in `hardware/switch_emg.ino` halfway between your resting noise and your weakest twitch, uploads it, then checks 20 s live. Stops without changing anything if rest and twitches overlap (pads/wiring). `--dry-run` measures only; `--check-only` re-uploads the sketch as it is and runs just the 20 s check. The sketch fires one DOWN per twitch: UP only below `THRESHOLD/2` (hysteresis) and no new DOWN within 400 ms. |
| `py -3.10 tools/twitch_to_space.py` | Every twitch presses **Space** (lockout 0.4 s against double triggers). Finds the Arduino port by itself (`--port COM3` to choose). `--fake`: no Arduino, Enter = a twitch. |

`hardware/switch_emg.ino` has two settings at the top: `THRESHOLD` (written by the
calibration) and `DEBUG` (`true` also prints raw/envelope/baseline numbers for the Arduino
IDE Serial Plotter; the tools ignore those lines). The Arduino IDE wants the sketch in a
folder of the same name — let it create one, or use the tools (they handle it).

### Two ways to connect it — use ONE at a time

Only one program can open a COM port. Close the Arduino Serial Monitor before either.

**Option A (simplest): twitch → Space key**
1. In `backend/.env`, leave `SWITCH_SERIAL_PORT=` **empty**.
2. Start the backend, open http://localhost:8000, set **Input** to *Scanning (single switch)*.
3. In a second terminal: `cd backend; py -3.10 tools/twitch_to_space.py`
4. Click the ReVoice browser tab so it is **focused** (Space goes to the window in front).
   The pill says "Switch: using spacebar" — correct, the twitches arrive as Space.

**Option B: backend serial bridge**
1. In `backend/.env`: `SWITCH_SERIAL_PORT=COM3` (your port) and `SWITCH_BAUD=9600`.
2. Do **not** run `twitch_to_space.py`.
3. Start the backend; it prints `[switch] connected on COM3`. Set Input to Scanning; the
   pill says **Switch: connected on COM3**. The tab doesn't need focus. If the port is
   missing or busy the backend warns and retries every 3 s.

**Fallback:** the **spacebar always works** in scanning mode (not while typing in the text
box). Without hardware you can also fake a switch press (Option B's path):
```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/switch/simulate -ContentType application/json -Body '{"state":"DOWN"}'
```

## Look and feel

- **Light and dark themes.** The page follows the computer's setting; the sun/moon button
  in the header switches and remembers the choice. The dark theme is meant for the bedside
  at night (no pure black/white glare). All text meets WCAG AA contrast in both themes.
- **The composed sentence is the biggest thing on screen**, in Atkinson Hyperlegible (a font
  made for low-vision readers; the system font is used if there's no internet).
- **Camera overlay** colours follow the theme, with solid chips behind letters so they stay
  readable over bright or dark video. The dwell ring glows as it fills and the chosen cell
  flashes briefly, so the patient sees each selection.
- **Animations are small and use only transform/opacity**; with the system's "reduce
  motion" setting they are switched off. Measured cost of the camera loop: about 2.5 ms
  per frame, the same as before the redesign.
- **Toasts** (bottom right) confirm actions; error toasts stay until closed, and the normal
  status text is always shown as well.

## Upload old recordings (for someone who has already lost their voice)

In the Voice Bank section click **Upload old recordings** (or create the user with
*Use old recordings*). Choose one or more files: mp3, m4a, wav, WhatsApp voice notes
(.opus/.ogg), webm or mp4 videos.

- Everything is decoded **in the browser** (no ffmpeg needed on Windows, no Colab changes).
  A file the browser can't read gets a clear message.
- Each file shows a waveform: drag the two **green handles** so only the person's own voice
  is kept (cut out other speakers, music, background chatter), press **▶ Preview** to check.
- Tick the consent box ("I am this person or their family/legal guardian and have the
  right to use these recordings") and enter your name, then **Save trimmed clips**. Who
  consented and when is stored with every clip.
- Only a clean mono 24 kHz WAV of the kept part is uploaded. Saved clips show their length
  and speech time; under 15 s of speech in total gives a warning.
- When an emotion has no recorded clip, speech uses the uploaded recordings (both F5-TTS and
  XTTS), before guided recordings. To use **only** the uploads, set *Voice reference* (below).
- The conversion (mix to mono, trim, resample to 24 kHz, 16-bit WAV) is our own tested code in
  `frontend/audio_prep.js`: a 440 Hz tone and a speech-like signal come out with the same
  pitch, length, level and sample rate (`frontend/tests/upload_conversion.test.mjs`).

## Voice reference: which recordings the voice is copied from

In the Voice Bank, **Voice reference** chooses, per user:

| Setting | Uses |
|---|---|
| Auto | clips recorded for the chosen emotion → else uploaded recordings → else guided takes |
| Uploaded recordings only | only the uploads; if there are none you get an error, never other clips. A second list picks **all uploads** (most speech first) or **one specific clip**; if that clip is deleted you get an error, never another upload |
| Recorded clips only | only the clips recorded for the chosen emotion |
| Guided takes only | only the guided takes chosen as voice references |

The Voice Bank shows which clips the **next** generation will use, and every result shows
*Voice copied from…* with the source, file name and length of each clip actually sent.

**F5 gets one clip, XTTS gets all of them.** F5 copies the voice from a single recording, so
only that one is sent (uploads: the one with most speech, or your chosen clip; recorded clips:
the longest). Colab then measures similarity against exactly the clip F5 copied, not against
other recordings it never heard. XTTS really uses every clip, so it gets them all.
(Inside that clip, F5 itself uses at most the first ~12 s, while similarity is measured on
the whole clip. Matching that exactly would need a notebook change, so it's left as is.)

**Test sentence** (next to *Generate & Speak*) speaks the exact sentence Colab's Step 4b uses,
with neutral emotion and the same request fields, so the website and Step 4b scores are
directly comparable. It isn't counted as a patient sentence in the selection evidence.

**Website vs Colab Step 4b check.** `py -3.10 parity_check.py <audio file> --profile <user id> --runs 3`
(in `backend`, with Colab running) sends the same file's exact bytes through the Step 4b
request and through the website's own code, and prints both similarity scores side by side,
plus what the website sends for that real user right now.

## Evidence for the report

- **Selection counter** (under the composed text): pointer selections used for the
  current sentence vs. spelling it letter by letter (one selection per letter and per
  space), and the % saved by predictions. Resets when you press **Clear**.
- **Generation log**: every *Generate & Speak* adds a line to
  `backend/data/generation_log.csv` (time, engine, emotion, voice source, text length in
  characters and words, generation time, success/failure, selection counts, and which
  reference clips were sent: setting, file names + lengths, total length, and F5's
  transcript of its reference). English sentences are not stored. For Indian languages the
  row also has the language, the mode (transliterate / translate / as typed), the typed
  text and the converted text, so a wrong conversion can be checked.
- **Voice metrics** (under each player, measured on Colab for every sentence):
  *similarity* to the real voice (speaker-embedding cosine, 0-1, higher = more alike),
  average *pitch* and range, *speaking rate* (words per second of actual speech), for the
  real voice and the output. Compare mode highlights the engine with the higher similarity.
- **Accent estimate** (hidden unless *Show accent estimates* is ticked under Speak; the
  choice is remembered in this browser). A model trained on 16 English accents gives a
  score from -1 to 1 for each; the page shows the **top 3 raw scores** for the real voice
  and for the output, no percentages (the scores are not probabilities). Confidence is how
  far the winner is ahead of second place: under 0.05 *low*, under 0.15 *medium*, else
  *high*. Under 3 s of speech it shows *n/a (too short)*. "Accent changed" is only claimed
  when both sides are long enough and at least medium confidence.
  On our own recordings every clip of 7 s or more was classified **Indian**, with US
  always second (0.03-0.22 behind); clips under 3 s flipped between Indian and US. So
  short generated sentences can't be judged, which is what made "US 100%" appear before.
- **Summary**: `cd backend; py -3.10 report.py` prints average generation time per engine
  and the average selections saved, plus average similarity and accent-match rate per
  engine (accent match counts only sentences where it could fairly be judged). The same numbers are at
  http://localhost:8000/stats/summary while the server runs.
  "Compare both" or speaking a sentence twice counts that sentence once for the savings.

## Known limitations (honest, worth mentioning in the report)

- **Recordings are stored unencrypted** in `backend/data/` on this computer (never in git).
  Fine for a prototype; a real product would encrypt them.
- **Laser detection needs decent lighting and a bright red dot** — red clothing or very
  bright rooms can confuse simple colour thresholding.
- **The EMG threshold is per person and per pad placement.** Re-run
  `tools/emg_calibrate.py` whenever the pads are moved; sweat or loose pads cause false
  triggers or missed twitches.
- **Colab free tier disconnects** after inactivity; re-run Steps 3, 3b, 3c, 3d, 4a, 5 and 6.
- **IndicF5 is slow on the free T4** (~15–20 s a sentence, at 32 steps) and its duration
  rule was only measured on Hindi. Translations come from Gemini and can be wrong: the
  converted text is shown so a caregiver can check it.
- **F5-TTS uses one reference clip** (the longest, trimmed to 12 s or less at a pause) plus a
  Whisper transcript of it, shown under the F5 player. If the transcript is wrong, F5's
  output suffers: re-record that clip more clearly.

## Licences
- **F5-TTS model weights are released for non-commercial use only** (fine for a college
  project, not for a product).
- XTTS-v2 is under the Coqui Public Model License (also non-commercial).
- **IndicF5** weights (AI4Bharat, gated on Hugging Face) are also for non-commercial use.
