# ReVoice switches: builds, wiring and setup

ReVoice can be driven by **one switch** (auto-scan: the light moves by itself, the switch
picks) or **two switches** (step scan: NEXT moves the light, SELECT picks; nothing moves on
its own, so the patient sets the pace). Every switch below ends up as the same signal:

| Switch | Sends | Read by |
|---|---|---|
| SELECT | `DOWN` / `UP` over USB, or **Space** over Bluetooth | backend bridge or `twitch_to_space.py`; Space key |
| NEXT   | `NEXT_DOWN` / `NEXT_UP` over USB, or **Enter** over Bluetooth | the same |

In ReVoice: **Input → Scanning (1 or 2 switches)**, then **Scanning settings → Switches: 1 or 2**.
The keyboard always works too: Space = SELECT, Enter or → = NEXT.

## Which build?

| Build | Sketch | Sensors | Scanning |
|---|---|---|---|
| Finger-twitch muscle sensor | `hardware/switch_emg.ino` (Uno) | EMG pads on the forearm | 1 switch |
| **Wristband**, wired | `hardware/switch_ir/switch_ir.ino` (Uno) | 2 IR sensors above two fingers | 2 switches (`TWO_SWITCH = true`) |
| **Glasses**, wired | `hardware/switch_ir/switch_ir.ino` (Uno) | 1 IR sensor at cheek or eyebrow | 1 switch (`TWO_SWITCH = false`) |
| Wristband or glasses, **wireless** | `hardware/switch_esp32_ble/switch_esp32_ble.ino` | same sensors, on 3V3 | as above |

The same sketches work with one or two sensors: only `TWO_SWITCH` changes.

## Settings at the top of the IR sketches

- `SENSOR_TYPE` (Uno) / `MODE` (ESP32): `DIGITAL_PROXIMITY` for FC-51 / E18-D80NK modules (OUT goes
  LOW when something is near), `ANALOG_TCRT5000` for analog modules; ESP32 also `TOUCH_TEST`.
- `TWO_SWITCH`: `true` for SELECT + NEXT, `false` for one sensor (SELECT only).
- `MIN_HOLD_MS` (default **120**): a press only counts if the sensor stays active this long,
  so brushing past or moving the hand doesn't type anything. Raise it (150–250) if you get
  presses you didn't mean; lower it (80) if deliberate presses are missed.
- Fixed rules, the same as `backend/tools/ir_logic.py`: 30 ms debounce, then the hold, then no
  new press within **300 ms** of the last one. Holding longer is still one press.
- `THRESHOLD` (analog only): set by `ir_calibrate.py --sensor analog`.

## Wiring

**Arduino Uno (wired, `switch_ir.ino`)**

| Sensor pin | Digital module (FC-51 / E18-D80NK) | Analog module (TCRT5000) |
|---|---|---|
| SELECT OUT | pin **2** | **A0** |
| NEXT OUT | pin **3** | **A1** |
| VCC | 5V | 5V |
| GND | GND | GND |

E18-D80NK wires: brown = VCC, blue = GND, black = OUT. The Uno's internal pull-up is switched
on, so the NPN output of the E18 works without an extra resistor.

**ESP32 (wireless, `switch_esp32_ble.ino`)**: power the sensors from **3V3** (ESP32 pins take at
most 3.3 V; FC-51 and TCRT5000 modules work at 3.3 V). The E18-D80NK needs a 5 V supply: use it
on the ESP32 only if its OUT has no pull-up to 5 V (powered from 5V with OUT unconnected, OUT
must not measure 5 V); otherwise use FC-51 modules with the ESP32.

| Mode | SELECT | NEXT |
|---|---|---|
| `TOUCH_TEST` (no sensors yet: touch a bare wire) | GPIO **4** | GPIO **15** |
| `DIGITAL_PROXIMITY` | GPIO **27** | GPIO **26** |
| `ANALOG_TCRT5000` (ADC1 only: ADC2 stops working while Bluetooth is on) | GPIO **34** | GPIO **35** |

The built-in LED (GPIO 2) blinks while waiting for Bluetooth and stays on once connected.

## Wristband build (two sensors, step scan)

The patient keeps the hand resting; **raising a finger** a little brings it into range of a
sensor above it. Middle finger = **SELECT**, index finger = **NEXT**.

1. **Strap**: a 2–3 cm wide elastic or Velcro strap (or a cheap watch strap) around the back
   of the hand, just behind the knuckles, snug but not tight (check the skin after 30 minutes).
2. **Wire arm**: per finger, a piece of stiff wire (1.5–2 mm craft or copper wire, or a bit of
   coat hanger) fixed to the strap with tape or a cable tie, bent into an L so it reaches over
   the finger. Put heat-shrink or tape over the end so nothing sharp touches the skin.
3. **Sensor**: hot-glue or cable-tie the module to the end of the arm, facing **down** at the
   back of the finger, about **2–3 cm above it** (between the knuckle and the first joint).
   The two sensors must not see each other's finger: keep them over the middle of each finger,
   angled slightly apart if needed.
4. **Tune** (below): with the hand resting, the finger must be **out of range** (module LED off);
   a small lift must bring it **in range** (LED on).
5. **Cables**: thin 3-core cable along the forearm, held with a second strap or a sleeve, so
   pulling the cable doesn't move the sensors.

Bending the wire changes the distance: re-check the tuning after adjusting.

## Glasses build (one sensor, auto-scan)

1. Use an old pair of frames (or safety glasses). Fix **one** module to the outer side of the
   frame with cable ties / hot glue / a small 3D-printed clip:
   - **eyebrow**: on the top rim, aimed at the eyebrow: raising the eyebrow = a press; or
   - **cheek**: on the lower rim near the hinge, aimed at the cheekbone: a cheek raise or
     half-smile = a press.
2. Aim it at the skin, **not into the eye**, and keep it at least 1 cm away from the face.
3. Tune so the resting face is out of range and the movement brings it in range. Blinking
   must not trigger it (`MIN_HOLD_MS` helps: blinks are short).
4. Route the cable along the temple arm and behind the ear; tape it down so its weight doesn't
   tilt the glasses. Set `TWO_SWITCH = false` and ReVoice to **Switches: 1**.

## Where to put the board and batteries

- **Wired (Uno)**: the Uno stays on the table next to the laptop, USB cable to the laptop;
  only the sensor cables go to the patient (1–1.5 m is fine).
- **Wireless (ESP32)**: put the ESP32 in a small box or pouch **on the forearm strap**
  (wristband) or **clipped to the collar / shirt pocket** (glasses), never on the glasses
  themselves. Power it from a small **USB power bank** in a pocket or an armband with a
  short cable. Some power banks switch off when the current is low: use one with an
  "always on" / low-current mode, or check that the ESP32 LED stays lit for 2 minutes.
- Nothing hot or heavy on the patient: a few grams on the strap, the power bank in a pocket.

## Tuning the potentiometer (digital modules)

The small screw (potentiometer) on the module sets the detection distance; the module's own
LED lights when it sees something. **Turn it until the LED lights at the distance you want**:
off with the finger resting / face relaxed, on with the small movement. Fine-tune with the
live view, which also counts presses with the sketch's real rules:

```powershell
cd backend
py -3.10 tools/ir_calibrate.py                 # live NEAR/far + presses (Ctrl+C to stop)
py -3.10 tools/ir_calibrate.py --one-switch    # glasses: SELECT only
py -3.10 tools/ir_calibrate.py --save          # writes TWO_SWITCH etc. into switch_ir.ino and uploads it
```

Analog TCRT5000 modules: `py -3.10 tools/ir_calibrate.py --sensor analog --save` measures rest
and 3 presses per switch and writes a `THRESHOLD` that worked on the recording. Bright sunlight
contains infrared: test in the room where it will be used and keep sensors out of direct sun.

## Uploading

```powershell
cd backend
py -3.10 tools/switch_upload.py --board uno --port COM3      # switch_ir.ino
py -3.10 tools/switch_upload.py --board esp32 --port COM7    # switch_esp32_ble.ino
```

**ESP32 one-time setup** (what we used: ESP32 core **3.3.12**, *ESP32 BLE Keyboard* **0.3.2**
by T-vK, *NimBLE-Arduino* **1.4.3**, built with `-DUSE_NIMBLE`). T-vK's library doesn't compile
on core 3.x with its default Bluetooth code (`std::string` vs `String` errors); its NimBLE
option does, and uses less memory, so no downgrade to core 2.0.17 is needed:

```powershell
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32
arduino-cli lib install "NimBLE-Arduino@1.4.3"
arduino-cli config set library.enable_unsafe_install true
arduino-cli lib install --git-url https://github.com/T-vK/ESP32-BLE-Keyboard.git
```

`switch_upload.py` adds the `-DUSE_NIMBLE` flag itself and lists anything missing. (In the
Arduino IDE instead: add `#define USE_NIMBLE` as the first line of the library's `BleKeyboard.h`.)

## Connecting to ReVoice

- **Wired, option B (recommended)**: `SWITCH_SERIAL_PORT=COM3` in `backend/.env`, restart the
  backend. It reads both switches; the pill says **Switch: connected on COM3**. Works even when
  the browser window isn't focused.
- **Wired, option A**: `SWITCH_SERIAL_PORT` empty and `py -3.10 tools/twitch_to_space.py --port COM3`
  (SELECT -> Space, NEXT -> Enter). The ReVoice tab must be the focused window.
- **Wireless (ESP32)**: pair it once with Windows: **Settings → Bluetooth & devices → Add device
  → Bluetooth → "ReVoice Switch"**. The ESP32 LED stays on when connected. It types Space and
  Enter like a keyboard, so the ReVoice tab must be the focused window (click it; ReVoice shows
  "Click here to resume scanning" if focus is lost). Check in Notepad first: SELECT types a
  space, NEXT a new line. Trouble: remove the device in Windows and pair again; power-cycle the
  ESP32.

## Which one for a demo?

- **Most reliable**: wired Uno + backend bridge (option B). No pairing, no batteries, the pill
  shows the connection, and `/switch/simulate` can fake a press if a sensor is knocked.
- **Most impressive**: ESP32 wristband, no cables to the laptop. Pair it before the audience
  arrives, keep a fully charged power bank, and keep the wired Uno as plan B.
- **Before the sensors arrive**: ESP32 in `TOUCH_TEST` mode (touch two wires), or just the
  keyboard (Space / Enter).

## When the sensors arrive: checklist

1. Check the modules: power one from 5V (Uno) with nothing connected to OUT; its power LED is
   on, and the detect LED lights when a hand is 2–5 cm away. Note which pin is OUT.
2. Wire SELECT only (pin 2 / GPIO 27). Upload the sketch with `TWO_SWITCH = false`,
   `SENSOR_TYPE`/`MODE = DIGITAL_PROXIMITY`.
3. `py -3.10 tools/ir_calibrate.py --one-switch`: turn the potentiometer until NEAR shows only
   for a deliberate movement; 10 deliberate presses = 10 counted, 1 minute of rest = 0.
4. Build the mount (wristband or glasses), then repeat step 3 **on the person**, in the room
   where it will be used. Adjust `MIN_HOLD_MS` if brushes count or real presses are missed.
5. Wristband: wire NEXT (pin 3 / GPIO 26), `TWO_SWITCH = true`, run `ir_calibrate.py` again and
   check the two fingers don't trigger each other.
6. `py -3.10 tools/ir_calibrate.py --save` (or `switch_upload.py`) to put the real sketch on the board.
7. ReVoice: **Input → Scanning**, **Switches: 1 or 2** to match. Spell a short word, pick a
   prediction, SPEAK. Wired: the pill says connected. Wireless: pair first (above).
8. Wear it for 10 minutes of normal resting: count unwanted presses (target: none). Check the
   skin under the strap and the comfort of the glasses.
9. Write down the final settings (`MIN_HOLD_MS`, potentiometer position, distances) and commit
   the sketch.
