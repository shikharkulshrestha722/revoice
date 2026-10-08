# ReVoice — Demo checklist: finger-twitch switch + scanning

A short script for showing scanning mode, with a plan B if the hardware fails.
Commands run in PowerShell from the `backend` folder, always with `py -3.10`.

## Before the demo (15 minutes)

1. **Wiring:** sensor SIG → A0, VCC → 5V, GND → GND. Arduino on USB.
2. **Pads (forearm):** palm side, a third of the way from elbow to wrist (the muscle that
   moves when you wiggle your fingers). Two signal pads 2–3 cm apart in a line along the
   forearm; reference pad on the bony point of the elbow. Clean, dry skin, pressed firmly.
3. **Close the Arduino Serial Monitor** (and anything else using the port).
4. **Find the port:** `py -3.10 -m serial.tools.list_ports -v` (look for Arduino or CH340).
5. **Calibrate:** `py -3.10 tools/emg_calibrate.py --port COM3`
   Relax when told, twitch on each prompt. It must end with "At rest: 0 DOWN" and about
   5 DOWNs while twitching. "overlap" = fix the pads and run it again.
6. **Choose ONE way to connect** (one program per COM port):
   - **Option A (simplest):** `SWITCH_SERIAL_PORT=` empty in `backend/.env`. After step 8,
     in a second terminal: `py -3.10 tools/twitch_to_space.py --port COM3`, then click the
     ReVoice tab so it's focused. Pill: "Switch: using spacebar" (twitches arrive as Space).
   - **Option B:** `SWITCH_SERIAL_PORT=COM3` in `backend/.env`, no twitch_to_space.py.
     Backend prints `[switch] connected on COM3`; pill: **Switch: connected on COM3**.
7. Start Colab (Steps 1 → 6) and the backend: `py -3.10 -m uvicorn app:app --reload`.
8. Open http://localhost:8000, pick the user, set **Input** to *Scanning (single switch)*.

## The demo (2–3 minutes)

1. Explain: "This patient can't point at all. One small finger twitch is enough."
2. Spell a short word: wait for the row, twitch, wait for the letter, twitch.
   Point out the **Get ready…** pause after each letter (time to relax the muscle).
3. Pick a **prediction** from the first row — show the "saved by prediction" figure.
4. Select **SPEAK**: scanning shows "Paused while speaking…" and resumes after the audio.
   The log records these sentences as `scanning`.
5. Show **Scanning settings**: slower steps for a tired patient, optional click sound.

## Plan B: hardware fails

- **The spacebar always works** in scanning mode — it's the same as a twitch. Keep demoing.
- Option A prints "busy" → the backend has the port: empty `SWITCH_SERIAL_PORT` and restart
  it, or close the Serial Monitor.
- Option B pill says **not connected (COM3)** → hover it to see why. *busy*: close the
  Serial Monitor / stop twitch_to_space.py (the backend retries every 3 s). *not found*:
  re-plug USB; if the port number changed, update `.env` and restart the backend.
- False triggers or missed twitches → press the pads on again, rerun `emg_calibrate.py`.
- Test the key path with no Arduino: `py -3.10 tools/twitch_to_space.py --fake`
  (Enter, then click the ReVoice tab within 3 s).
- Fake a switch press for Option B from another terminal:
  ```powershell
  Invoke-RestMethod -Method Post -Uri http://localhost:8000/switch/simulate -ContentType application/json -Body '{"state":"DOWN"}'
  ```
