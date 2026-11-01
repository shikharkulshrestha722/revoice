// ReVoice IR switches (wired, Arduino Uno): IR proximity sensors -> lines over USB at 9600 baud.
//   SELECT sensor: "DOWN" / "UP"            (the same protocol as the EMG switch)
//   NEXT sensor:   "NEXT_DOWN" / "NEXT_UP"  (only with TWO_SWITCH = true)
// Read by backend/switch_bridge.py (SWITCH_SERIAL_PORT=COMx) or backend/tools/twitch_to_space.py.
//
// Mountings (hardware/HARDWARE.md):
//   wristband: sensors 2-3 cm above the middle finger (SELECT) and index finger (NEXT);
//              RAISING a finger brings it into range = a press. TWO_SWITCH = true -> step scan.
//   glasses:   ONE sensor near the cheek or eyebrow = SELECT. TWO_SWITCH = false -> auto-scan.
//
// Wiring, DIGITAL_PROXIMITY (FC-51 / E18-D80NK style, 3 pins, OUT goes LOW when something is near):
//   SELECT OUT -> pin 2, NEXT OUT -> pin 3, VCC -> 5V, GND -> GND.
// Wiring, ANALOG_TCRT5000 (analog OUT changes when something is near):
//   SELECT A0 -> A0, NEXT A0 -> A1, VCC -> 5V, GND -> GND. Keep fingers/face AWAY for 2 s after reset.

// ===================== SETTINGS =====================
#define DIGITAL_PROXIMITY 1
#define ANALOG_TCRT5000   2
const int SENSOR_TYPE = DIGITAL_PROXIMITY;   // which kind of sensor module is connected
const bool TWO_SWITCH = true;                // false = one sensor (SELECT only): 1-switch auto-scan
const unsigned long MIN_HOLD_MS = 120;       // a press counts only if the sensor stays active this
                                             // long (ignores brushes and hand movement)
const int THRESHOLD = 100;                   // ANALOG only: change from the resting value that means
                                             // "near" (0-1023 units; backend/tools/ir_calibrate.py)
const bool DEBUG = false;                    // true: also print "select,next" readings for the
                                             // Serial Plotter (the ReVoice tools ignore those lines)
// =====================================================

// Same rules as backend/tools/ir_logic.py (tests check these numbers match).
const unsigned long DEBOUNCE_MS = 30;        // a reading must stay the same this long to count
const unsigned long REFRACTORY_MS = 300;     // no new press within 300 ms of the previous one
const int RELEASE = THRESHOLD / 2;           // ANALOG: "near" ends only below half the threshold

const int SELECT_PIN = 2, NEXT_PIN = 3;              // DIGITAL
const int SELECT_ANALOG = A0, NEXT_ANALOG = A1;      // ANALOG

// One switch: debounce -> hold for MIN_HOLD_MS -> DOWN (unless too soon after the last DOWN);
// released -> UP. One press = one DOWN, however long it is held.
struct Switch {
  const char *downText;
  const char *upText;
  float baseline;              // ANALOG: resting value, measured in setup()
  bool reading;                // latest raw "near" reading
  unsigned long readingSince;  // when that reading started
  bool active;                 // debounced "near"
  unsigned long activeSince;   // when the current active stretch started
  bool pressed;                // DOWN sent, UP not yet
  bool anyDown;                // no DOWN yet -> no refractory wait for the first one
  unsigned long lastDownMs;
};

Switch selectSwitch = {"DOWN", "UP"};
Switch nextSwitch = {"NEXT_DOWN", "NEXT_UP"};

void updateSwitch(Switch &s, bool near, unsigned long now) {
  if (near != s.reading) { s.reading = near; s.readingSince = now; }
  // debounce: take the reading only once it has stayed the same for DEBOUNCE_MS
  if (s.reading != s.active && now - s.readingSince >= DEBOUNCE_MS) {
    s.active = s.reading;
    s.activeSince = s.readingSince;          // the hold is timed from the real start
  }
  // still near right now: a short brush must not count just because the release is being debounced
  bool held = s.active && s.reading && now - s.activeSince >= MIN_HOLD_MS;
  bool rested = !s.anyDown || now - s.lastDownMs >= REFRACTORY_MS;
  if (!s.pressed && held && rested) {
    s.pressed = true; s.anyDown = true; s.lastDownMs = now;
    Serial.println(s.downText);
  } else if (s.pressed && !s.active) {
    s.pressed = false;
    Serial.println(s.upText);
  }
}

// ANALOG: near when the reading moves THRESHOLD away from rest; stays near until below RELEASE.
bool analogNear(Switch &s, int value) {
  float change = abs(value - s.baseline);
  return s.reading ? change > RELEASE : change > THRESHOLD;
}

float restingValue(int pin) {
  long sum = 0;
  for (int i = 0; i < 200; i++) { sum += analogRead(pin); delay(10); }   // 2 s
  return sum / 200.0;
}

void setup() {
  Serial.begin(9600);
  if (SENSOR_TYPE == DIGITAL_PROXIMITY) {
    pinMode(SELECT_PIN, INPUT_PULLUP);
    pinMode(NEXT_PIN, INPUT_PULLUP);
  } else {
    selectSwitch.baseline = restingValue(SELECT_ANALOG);
    if (TWO_SWITCH) nextSwitch.baseline = restingValue(NEXT_ANALOG);
  }
}

void loop() {
  unsigned long now = millis();
  int selectRaw, nextRaw = 0;
  bool selectNear, nextNear = false;
  if (SENSOR_TYPE == DIGITAL_PROXIMITY) {
    selectRaw = digitalRead(SELECT_PIN);
    selectNear = selectRaw == LOW;                    // OUT goes LOW when something is near
    if (TWO_SWITCH) { nextRaw = digitalRead(NEXT_PIN); nextNear = nextRaw == LOW; }
  } else {
    selectRaw = analogRead(SELECT_ANALOG);
    selectNear = analogNear(selectSwitch, selectRaw);
    if (TWO_SWITCH) { nextRaw = analogRead(NEXT_ANALOG); nextNear = analogNear(nextSwitch, nextRaw); }
  }
  updateSwitch(selectSwitch, selectNear, now);
  if (TWO_SWITCH) updateSwitch(nextSwitch, nextNear, now);
  if (DEBUG) { Serial.print(selectRaw); Serial.print(','); Serial.println(nextRaw); }
  delay(5);
}
