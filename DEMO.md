# ReVoice — Demo checklist: switches + scanning (+ Indian languages)

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

## Caregiver PIN (1 minute)

1. "A cloned voice can be misused, so the recordings are behind a caregiver PIN." Point at
   the indicator: **Recordings locked**.
2. Press **SPEAK** (or a prediction): it speaks at once. The patient never needs the PIN.
3. Open **Guided voice banking**: the PIN dialog appears. Type a wrong PIN once ("4 attempts
   left before a 5-minute lockout"), then the right one: *Recordings unlocked · 15 min*.
4. **Audit log**: the wrong attempt and the unlock are listed, with no PIN in it.
5. **Lock now**: the panel closes and the indicator says locked again.

## IR switches: wristband (2 switches) or glasses (1 switch)

Build, wiring, tuning and the "when the sensors arrive" checklist: `hardware/HARDWARE.md`.

Before: sensors tuned on the person (`py -3.10 tools/ir_calibrate.py`, 10 presses = 10 counted,
1 minute of rest = 0). Wired Uno: `SWITCH_SERIAL_PORT=COM3` in `backend/.env`, pill **connected**.
Wireless ESP32: paired as "ReVoice Switch" (LED solid), ReVoice tab focused, power bank charged.

1. Wristband: **Scanning settings → Switches: 2**. "She raises her index finger to move, her
   middle finger to pick. Nothing moves until she does, so there's no time pressure."
2. Spell a short word: NEXT to the row, SELECT, NEXT to the letter, SELECT. Show **↩ ROWS**
   after a wrong row. Pick a prediction from the first row, then SPEAK.
3. Glasses (one sensor): **Switches: 1**, the light moves by itself; a cheek raise picks.
4. Show that brushing the hand past a sensor doesn't type anything (`MIN_HOLD_MS`).

Plan B: keyboard (Space = SELECT, Enter / → = NEXT), or fake presses from a terminal:
`Invoke-RestMethod -Method Post -Uri http://localhost:8000/switch/simulate -ContentType application/json -Body '{"state":"NEXT_DOWN"}'`

## Indian languages: IndicF5 (2 minutes)

Before: `HF_TOKEN` secret in Colab and **Step 3d** run (see README, "Indian languages");
`GEMINI_API_KEY` in `backend/.env`. Make one Hindi sentence before the audience arrives, so
the model is already loaded (the first one takes longer).

1. Explain: "Her voice was recorded in English. She also speaks Hindi at home."
2. Under **Speak**: engine **IndicF5 (Indian languages)**, Language **Hindi**,
   *Typed text is* → **English, translate it**.
3. Compose **I need water** (pointer, scanning or a prediction) and press SPEAK.
   The status says "Generating in Hindi… about 15 s"; scanning waits as usual.
4. Point at the Hindi text above the player (मुझे पानी चाहिए।): this is exactly what was
   spoken, so a caregiver can check the translation. Similarity to her real voice is below.
5. Switch to **the language in English letters** and type `main thik hoon`: transliterated,
   not translated.
6. **Compare IndicF5 vs F5**: the same Hindi sentence through plain F5, which was never
   trained on Hindi, to show why IndicF5 is needed.

Plan B: if IndicF5 isn't set up or Colab is slow, show the Hindi text from a previous run in
the generation log (`converted_text` column) and play a saved recording. No Gemini key →
type the sentence in Devanagari (e.g. `मुझे पानी चाहिए`) and it is spoken as typed.

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
