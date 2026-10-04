# ReVoice — Setup Guide

Assistive communication for people with paralysis/ALS: select letters by pointing,
GenAI predicts the phrase, and it is spoken in the patient's own cloned voice.

## What's in this folder

```
revoice/
├── frontend/       the webpage (webcam, letter grid, predictions, voice recorder)
├── backend/        the Python server connecting frontend <-> Colab and Gemini
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
6. Run Step 6. The last line must say **ReVoice API v4**.

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
```

## How to use it

1. Click **Start Camera** and allow webcam access.
2. Choose **Finger** or **Laser** tracking. Hold the pointer still on a letter until the
   green ring fills (~1.5 s) to select it. The top row shows 3 AI-predicted phrases —
   dwell on one to use it.
3. Pick an emotion, click **Record Voice Clip**, and speak naturally in that tone
   (6–20 s; click again to stop early). 2–3 clips per emotion works best.
4. Pick a voice engine — **XTTS-v2**, **F5-TTS**, or **Compare both** (generates with
   each, one after the other, and shows how long each took) — then **Generate & Speak**.

## Known limitations (honest, worth mentioning in the report)

- **Voice bank resets on backend restart** — stored in memory for now (moving to disk next).
- **Laser detection needs decent lighting and a bright red dot** — red clothing or very
  bright rooms can confuse simple colour thresholding.
- **Colab free tier disconnects** after inactivity; re-run Steps 3, 3b and 6.

## Licences
- **F5-TTS model weights are released for non-commercial use only** (fine for a college
  project, not for a product).
- XTTS-v2 is under the Coqui Public Model License (also non-commercial).
