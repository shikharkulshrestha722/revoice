# ReVoice — Setup Guide

## What's in this folder

```
revoice/
├── frontend/       the webpage (webcam, letter grid, voice recorder)
├── backend/        the Python server connecting frontend <-> Colab
└── colab/          the notebook that runs voice cloning on Google Colab
```

## How to run it (in order)

### 1. Start the Colab notebook first
1. Upload `colab/xtts_server.ipynb` to https://colab.research.google.com
2. Go to **Runtime → Change runtime type → GPU**
3. Get a free ngrok token: https://dashboard.ngrok.com/get-started/your-authtoken
4. Paste it into the cell that says `NGROK_AUTH_TOKEN = "..."`
5. Run every cell top to bottom
6. The last cell will print a URL like `https://xxxx.ngrok-free.app` — **copy it**

### 2. Update the backend with that URL
Open `backend/app.py` and replace this line with your copied URL:
```python
COLAB_API_URL = "https://REPLACE-WITH-YOUR-NGROK-URL.ngrok-free.app"
```

### 3. Install and run the backend
```bash
cd backend
pip install -r requirements.txt
uvicorn app:app --reload
```

### 4. Open the app
Go to **http://localhost:8000** in your browser.

## How to actually use it

1. Click **Start Camera** — allow webcam access
2. Point a laser pointer (or your finger, for testing) at the grid — hold still on a letter for ~1.5 seconds to select it
3. Pick an emotion from the dropdown, click **Record 5s Reference Clip**, and speak a sentence in that tone
4. Once you've composed a sentence and recorded at least one voice clip, click **Generate & Speak**

## Known limitations (mention these in your report — they're honest, not flaws to hide)

- **Colab's URL changes every session** (free ngrok tier) — you'll re-copy it each time you restart the notebook
- **Voice bank resets on backend restart** — it's stored in memory for this prototype; a real version would save to disk/database
- **Laser detection needs decent lighting and a genuinely bright red dot** — very bright rooms or red clothing in frame can cause false detections; this is a known limitation of simple color-thresholding and worth mentioning as future work (e.g., using a specific wavelength filter)
- **GenAI predictive composition (Gemini) isn't wired in yet** — this version composes letter-by-letter; that's the next piece to add

## What to build next

1. Add GenAI phrase prediction between the grid and the composed text box
2. Add the rule-based safety checks (e.g., flag emergency keywords)
3. Add a simple caregiver "Confirm / Override" step before speaking
4. Save the voice bank to disk instead of memory
