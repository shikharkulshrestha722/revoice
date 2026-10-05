# ReVoice — Setup Guide

Assistive communication for people with paralysis/ALS: select letters by pointing,
GenAI predicts the phrase, and it is spoken in the patient's own cloned voice.

## What's in this folder

```
revoice/
├── frontend/       the webpage (webcam, letter grid, predictions, voice recorder)
├── backend/        the Python server connecting frontend <-> Colab and Gemini
│   ├── data/       users and their recordings (created automatically, not in git)
│   └── tests/      automated tests (Colab and Gemini are faked)
└── colab/
    ├── revoice_server_v2.ipynb   the voice server (XTTS-v2 + F5-TTS) — the ONLY one to use
    └── archive/                  old notebooks — do NOT run these
```

> **Only `colab/revoice_server_v2.ipynb` is valid.** Running cells from older notebooks
> in the same Colab session breaks the installed packages and steals the ngrok URL.

## How to run it (in order)

### 1. Start the Colab notebook
1. Upload `colab/revoice_server_v2.ipynb` to https://colab.research.google.com
2. **Runtime → Change runtime type → T4 GPU**
3. Run Step 1 and Step 2 (install). Then **Runtime → Restart session**.
4. Run Step 2b (version check), Step 3, Step 3b, Step 4.
5. Paste your ngrok token (https://dashboard.ngrok.com/get-started/your-authtoken) in Step 5.
6. Run Step 6. The last line must say **ReVoice API v5**.

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
6. Pick a voice engine — **XTTS-v2**, **F5-TTS**, or **Compare both** (generates with
   each, one after the other, and shows how long each took) — then **Generate & Speak**.

## Evidence for the report

- **Selection counter** (under the composed text): pointer selections used for the
  current sentence vs. spelling it letter by letter (one selection per letter and per
  space), and the % saved by predictions. Resets when you press **Clear**.
- **Generation log**: every *Generate & Speak* adds a line to
  `backend/data/generation_log.csv` (time, engine, emotion, voice source, text length in
  characters and words, generation time, success/failure, selection counts). The sentence
  itself is not stored.
- **Summary**: `cd backend; py -3.10 report.py` prints average generation time per engine
  and the average selections saved. The same numbers are at
  http://localhost:8000/stats/summary while the server runs.
  "Compare both" or speaking a sentence twice counts that sentence once for the savings.

## Known limitations (honest, worth mentioning in the report)

- **Recordings are stored unencrypted** in `backend/data/` on this computer (never in git).
  Fine for a prototype; a real product would encrypt them.
- **Laser detection needs decent lighting and a bright red dot** — red clothing or very
  bright rooms can confuse simple colour thresholding.
- **Colab free tier disconnects** after inactivity; re-run Steps 3, 3b and 6.

## Licences
- **F5-TTS model weights are released for non-commercial use only** (fine for a college
  project, not for a product).
- XTTS-v2 is under the Coqui Public Model License (also non-commercial).
