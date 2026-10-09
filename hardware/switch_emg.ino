// ReVoice single switch: EMG muscle sensor -> prints "DOWN" / "UP" over USB at 9600 baud.
// Wiring: sensor SIG -> A0, VCC -> 5V, GND -> GND. Keep still for 2 s after reset (baseline).
// Read by backend/tools/twitch_to_space.py (Space key) OR backend/switch_bridge.py (SWITCH_SERIAL_PORT).

// ===================== SETTINGS (backend/tools/emg_calibrate.py rewrites these) =====================
const int THRESHOLD = 144;   // how far the muscle signal must rise above rest to count as a twitch
const bool DEBUG = false;     // true: also print "raw,envelope,baseline" for the Serial Plotter
                              // (the ReVoice tools ignore those lines; only DOWN/UP matter)
// ====================================================================================================

// Anti-chatter (one twitch = one DOWN). emg_calibrate.py replays exactly these rules.
const int RELEASE = THRESHOLD / 2;          // hysteresis: UP only when the signal falls below this
const unsigned long REFRACTORY_MS = 400;    // no new DOWN within 400 ms of the previous DOWN

const int EMG_PIN = A0;
float baseline = 0, envelope = 0;
const float ENVELOPE_ALPHA = 0.1;
bool pressed = false;
bool anyDown = false;          // no DOWN yet -> no refractory wait for the first one
unsigned long lastDownMs = 0;
void setup() {
  Serial.begin(9600);
  long sum = 0;
  for (int i = 0; i < 200; i++) { sum += analogRead(EMG_PIN); delay(10); }
  baseline = sum / 200.0; envelope = baseline;
}
void loop() {
  int raw = analogRead(EMG_PIN);
  envelope = envelope * (1 - ENVELOPE_ALPHA) + raw * ENVELOPE_ALPHA;
  if (!pressed) baseline = baseline * 0.995 + envelope * 0.005;
  float level = envelope - baseline;
  unsigned long now = millis();
  bool rested = !anyDown || (now - lastDownMs >= REFRACTORY_MS);
  if (!pressed && level > THRESHOLD && rested) {
    pressed = true; anyDown = true; lastDownMs = now; Serial.println("DOWN");
  } else if (pressed && level < RELEASE) {
    pressed = false; Serial.println("UP");
  }
  if (DEBUG) {
    Serial.print(raw); Serial.print(','); Serial.print(envelope); Serial.print(','); Serial.println(baseline);
  }
  delay(5);
}
