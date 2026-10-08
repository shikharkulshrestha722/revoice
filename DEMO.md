# ReVoice — Demo checklist: single-switch scanning

A short script for showing scanning mode, with a plan B if the hardware fails.

## Before the demo (10 minutes)

1. **Arduino + EMG sensor wired**: SIG → A0, VCC → 5V, GND → GND. Electrodes on a muscle
   the person can tense easily (forearm, or a cheek/eyebrow muscle).
2. `hardware/switch_emg.ino` uploaded. After plugging in, keep the muscle relaxed for ~2 s
   (it measures the resting level).
3. Check in the Arduino IDE Serial Monitor (9600 baud): tense → `DOWN`, relax → `UP`.
   **Close the Serial Monitor afterwards** — otherwise the backend can't open the port
   ("COM3 is busy" warning).
4. `backend/.env` has `SWITCH_SERIAL_PORT=COM3` (your port number) and `SWITCH_BAUD=9600`.
5. Start Colab (Steps 1 → 6) and the backend:
   ```powershell
   cd backend
   py -3.10 -m uvicorn app:app --reload
   ```
   Look for `[switch] connected on COM3` in the terminal.
6. Open http://localhost:8000, pick the user, set **Input** to *Scanning (single switch)*.
   The pill must say **Switch: connected on COM3** (green).

## The demo (2–3 minutes)

1. Explain: "This patient can't point at all. One muscle twitch is enough."
2. Spell a short word: wait for the row, tense, wait for the letter, tense.
   Point out the **Get ready…** pause after each letter (time to relax the muscle).
3. Pick a **prediction** from the first row — show the "saved by prediction" figure.
4. Select **SPEAK**: scanning shows "Paused while speaking…" and resumes after the audio.
5. Show **Scanning settings**: slower steps for a tired patient, optional click sound.

## Plan B: hardware fails

- **Spacebar works the whole time** — it's the same as the switch. Just keep demoing.
- Pill says **not connected (COM3)**: hover it to see why.
  - *busy* → close the Arduino Serial Monitor (or Arduino IDE). The backend retries every
    3 s; no restart needed.
  - *not found* → re-plug the USB cable, check the port in Device Manager; if it changed,
    update `SWITCH_SERIAL_PORT` and restart the backend.
- Switch presses that come too often (noisy electrodes) → re-seat the electrodes and reset
  the Arduino with the muscle relaxed. Presses closer than 0.3 s are ignored anyway.
- Fake presses from another terminal (looks exactly like the hardware to the page):
  ```powershell
  Invoke-RestMethod -Method Post -Uri http://localhost:8000/switch/simulate -ContentType application/json -Body '{"state":"DOWN"}'
  ```
