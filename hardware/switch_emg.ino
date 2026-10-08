// ReVoice single switch: EMG muscle sensor -> prints "DOWN" / "UP" over USB at 9600 baud.
// Wiring: sensor SIG -> A0, VCC -> 5V, GND -> GND. Keep still for 2 s after reset (baseline).
// The backend (backend/switch_bridge.py) reads these lines; set SWITCH_SERIAL_PORT in backend/.env.

const int EMG_PIN = A0;
float baseline = 0, envelope = 0;
const float ENVELOPE_ALPHA = 0.1;
const int THRESHOLD = 60;
bool pressed = false;
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
  bool active = (envelope - baseline) > THRESHOLD;
  if (active && !pressed) { pressed = true; Serial.println("DOWN"); }
  else if (!active && pressed) { pressed = false; Serial.println("UP"); }
  delay(5);
}
