// ReVoice wireless switch (ESP32, Bluetooth keyboard "ReVoice Switch"):
//   SELECT -> Space, NEXT -> Enter  (what ReVoice's scanning mode listens for)
// It also prints "DOWN"/"UP" and "NEXT_DOWN"/"NEXT_UP" over USB at 9600 baud (same protocol as the
// wired switches), handy for testing with a cable.
//
// Build (hardware/HARDWARE.md): ESP32 core 3.x + libraries "ESP32 BLE Keyboard" 0.3.2 (T-vK) and
// "NimBLE-Arduino" 1.4.3, compiled with -DUSE_NIMBLE (the T-vK library's own Bluetooth code does not
// compile on core 3.x; its NimBLE option does, and uses less memory):
//   py -3.10 backend/tools/switch_upload.py --board esp32 --port COM7
//
// Mountings: wristband (SELECT above the middle finger, NEXT above the index finger; raise a finger
// to press) or glasses (ONE sensor near the cheek/eyebrow, TWO_SWITCH = false).
// Sensors are powered from 3V3 (NOT 5V: the ESP32's pins take at most 3.3 V).

#include <BleKeyboard.h>

// ===================== SETTINGS =====================
#define TOUCH_TEST        1   // no sensors yet: touch a wire on GPIO 4 (SELECT) / GPIO 15 (NEXT)
#define DIGITAL_PROXIMITY 2   // FC-51 / E18-D80NK style: OUT goes LOW when near. GPIO 18 / GPIO 19
#define ANALOG_TCRT5000   3   // analog OUT: GPIO 34 / GPIO 35 (ADC1, 0-4095)
const int MODE = TOUCH_TEST;
const bool TWO_SWITCH = true;               // false = one sensor (SELECT only): 1-switch auto-scan
const unsigned long MIN_HOLD_MS = 120;      // a press counts only if the sensor stays active this long
const int ANALOG_THRESHOLD = 400;           // ANALOG: change from rest that means "near" (0-4095 units)
const float TOUCH_DROP = 0.3;               // TOUCH: touched when the reading falls 30% below rest
const bool DEBUG = false;                   // true: print "select,next" readings over USB
// =====================================================

// Same rules as switch_ir.ino and backend/tools/ir_logic.py.
const unsigned long DEBOUNCE_MS = 30;
const unsigned long REFRACTORY_MS = 300;

const int SELECT_TOUCH = 4, NEXT_TOUCH = 15;        // TOUCH_TEST (T0, T3)
const int SELECT_DIGITAL = 18, NEXT_DIGITAL = 19;   // DIGITAL_PROXIMITY
const int SELECT_ANALOG = 34, NEXT_ANALOG = 35;     // ANALOG_TCRT5000 (ADC1 works while Bluetooth is on)
const int LED_PIN = 2;                              // built-in LED: on = connected, blinking = waiting

BleKeyboard bleKeyboard("ReVoice Switch", "ReVoice", 100);

struct Switch {
  const char *downText;
  const char *upText;
  uint8_t key;                 // key typed on each press
  int pin;
  float baseline;              // TOUCH / ANALOG: resting value, measured in setup()
  float threshold;             // TOUCH / ANALOG: change that means "near"
  bool reading;
  unsigned long readingSince;
  bool active;
  unsigned long activeSince;
  bool pressed;
  bool anyDown;
  unsigned long lastDownMs;
};

Switch selectSwitch = {"DOWN", "UP", ' '};
Switch nextSwitch = {"NEXT_DOWN", "NEXT_UP", KEY_RETURN};

// debounce -> hold for MIN_HOLD_MS -> one key press (unless too soon after the last one)
void updateSwitch(Switch &s, bool near, unsigned long now) {
  if (near != s.reading) { s.reading = near; s.readingSince = now; }
  if (s.reading != s.active && now - s.readingSince >= DEBOUNCE_MS) {
    s.active = s.reading;
    s.activeSince = s.readingSince;
  }
  // still near right now: a short brush must not count just because the release is being debounced
  bool held = s.active && s.reading && now - s.activeSince >= MIN_HOLD_MS;
  bool rested = !s.anyDown || now - s.lastDownMs >= REFRACTORY_MS;
  if (!s.pressed && held && rested) {
    s.pressed = true; s.anyDown = true; s.lastDownMs = now;
    Serial.println(s.downText);
    if (bleKeyboard.isConnected()) bleKeyboard.write(s.key);   // keys only when paired + connected
  } else if (s.pressed && !s.active) {
    s.pressed = false;
    Serial.println(s.upText);
  }
}

int readRaw(const Switch &s) {
  if (MODE == TOUCH_TEST) return touchRead(s.pin);
  if (MODE == ANALOG_TCRT5000) return analogRead(s.pin);
  return digitalRead(s.pin);
}

// near: DIGITAL = pin LOW; TOUCH = reading dropped; ANALOG = reading moved. Stays near until the
// change falls below half the threshold (hysteresis), so a wobbly finger gives one press.
bool isNear(const Switch &s, int raw) {
  if (MODE == DIGITAL_PROXIMITY) return raw == LOW;
  float change = MODE == TOUCH_TEST ? s.baseline - raw : fabs(raw - s.baseline);
  return s.reading ? change > s.threshold / 2 : change > s.threshold;
}

void setUp(Switch &s, int pin) {
  s.pin = pin;
  if (MODE == DIGITAL_PROXIMITY) { pinMode(pin, INPUT_PULLUP); return; }
  long sum = 0;
  for (int i = 0; i < 100; i++) { sum += readRaw(s); delay(20); }   // 2 s at rest
  s.baseline = sum / 100.0;
  s.threshold = MODE == TOUCH_TEST ? s.baseline * TOUCH_DROP : ANALOG_THRESHOLD;
}

void setup() {
  Serial.begin(9600);
  pinMode(LED_PIN, OUTPUT);
  setUp(selectSwitch, MODE == TOUCH_TEST ? SELECT_TOUCH : MODE == ANALOG_TCRT5000 ? SELECT_ANALOG : SELECT_DIGITAL);
  if (TWO_SWITCH)
    setUp(nextSwitch, MODE == TOUCH_TEST ? NEXT_TOUCH : MODE == ANALOG_TCRT5000 ? NEXT_ANALOG : NEXT_DIGITAL);
  bleKeyboard.begin();
}

void loop() {
  unsigned long now = millis();
  bool connected = bleKeyboard.isConnected();
  digitalWrite(LED_PIN, connected || (now / 500) % 2 ? HIGH : LOW);   // solid = connected
  int selectRaw = readRaw(selectSwitch);
  updateSwitch(selectSwitch, isNear(selectSwitch, selectRaw), now);
  int nextRaw = 0;
  if (TWO_SWITCH) {
    nextRaw = readRaw(nextSwitch);
    updateSwitch(nextSwitch, isNear(nextSwitch, nextRaw), now);
  }
  if (DEBUG) { Serial.print(selectRaw); Serial.print(','); Serial.println(nextRaw); }
  delay(5);
}
