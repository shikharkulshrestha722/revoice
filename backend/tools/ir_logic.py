"""The IR switch sketches' rules in Python (hardware/switch_ir/switch_ir.ino and
hardware/switch_esp32_ble/switch_esp32_ble.ino), so ir_calibrate.py counts presses exactly like
the board, and tests can check the rules. Times are in milliseconds.

One press: the sensor reading must stay the same for DEBOUNCE_MS, then stay "near" for
MIN_HOLD_MS (brushes and hand movement are ignored); no new press within REFRACTORY_MS of the
last one. Releasing gives the UP. Holding longer is still ONE press.
"""

import re

DEBOUNCE_MS = 30
REFRACTORY_MS = 300
MIN_HOLD_MS = 120          # default; the sketches' MIN_HOLD_MS setting

SENSOR_TYPES = {"digital": "DIGITAL_PROXIMITY", "analog": "ANALOG_TCRT5000"}


class PressDetector:
    """One switch, the same steps as updateSwitch() in the sketches."""

    def __init__(self, min_hold_ms=MIN_HOLD_MS, debounce_ms=DEBOUNCE_MS, refractory_ms=REFRACTORY_MS):
        self.min_hold_ms = min_hold_ms
        self.debounce_ms = debounce_ms
        self.refractory_ms = refractory_ms
        self.reading = False           # latest raw "near" reading
        self.reading_since = 0
        self.active = False            # debounced "near"
        self.active_since = 0
        self.pressed = False
        self.last_down = None
        self.presses = 0

    def update(self, near, now):
        """Returns "DOWN", "UP" or None for this reading."""
        if near != self.reading:
            self.reading, self.reading_since = near, now
        if self.reading != self.active and now - self.reading_since >= self.debounce_ms:
            self.active = self.reading
            self.active_since = self.reading_since       # the hold is timed from the real start
        # still near right now: a short brush must not count just because the release is being debounced
        held = self.active and self.reading and now - self.active_since >= self.min_hold_ms
        rested = self.last_down is None or now - self.last_down >= self.refractory_ms
        if not self.pressed and held and rested:
            self.pressed, self.last_down = True, now
            self.presses += 1
            return "DOWN"
        if self.pressed and not self.active:
            self.pressed = False
            return "UP"
        return None


def analog_near(value, baseline, threshold, was_near):
    """ANALOG: near when the reading moves `threshold` away from rest; once near, it stays near
    until the change falls below threshold / 2 (whole-number division, as on the Uno)."""
    change = abs(value - baseline)
    release = threshold // 2 if isinstance(threshold, int) else threshold / 2
    return change > release if was_near else change > threshold


def replay(samples, threshold=None, baseline=None, min_hold_ms=MIN_HOLD_MS):
    """Runs one switch over recorded (time_ms, value) samples. Digital: value 1 = near.
    Analog: give threshold and baseline. Returns [(time_ms, "DOWN"|"UP")]."""
    detector = PressDetector(min_hold_ms)
    events = []
    for t, value in samples:
        near = bool(value) if threshold is None else analog_near(value, baseline, threshold, detector.reading)
        event = detector.update(near, t)
        if event:
            events.append((t, event))
    return events


# Reading and changing the settings block at the top of switch_ir.ino

def sketch_settings(text):
    """{'sensor': 'digital'|'analog', 'two_switch': bool, 'min_hold_ms': int, 'threshold': int}"""
    def find(pattern):
        match = re.search(pattern, text)
        if not match:
            raise ValueError(f"switch_ir.ino has no line matching {pattern!r}")
        return match.group(1)
    sensor = find(r"const\s+int\s+SENSOR_TYPE\s*=\s*(\w+)\s*;")
    return {"sensor": "analog" if sensor == "ANALOG_TCRT5000" else "digital",
            "two_switch": find(r"const\s+bool\s+TWO_SWITCH\s*=\s*(true|false)") == "true",
            "min_hold_ms": int(find(r"const\s+unsigned\s+long\s+MIN_HOLD_MS\s*=\s*(\d+)")),
            "threshold": int(find(r"const\s+int\s+THRESHOLD\s*=\s*(\d+)"))}


def set_sketch_settings(text, sensor=None, two_switch=None, threshold=None):
    """Rewrites only the given settings (comments kept); DEBUG is always switched off."""
    changes = [(r"(const\s+bool\s+DEBUG\s*=\s*)(true|false)", r"\g<1>false")]
    if sensor is not None:
        changes.append((r"(const\s+int\s+SENSOR_TYPE\s*=\s*)\w+", rf"\g<1>{SENSOR_TYPES[sensor]}"))
    if two_switch is not None:
        changes.append((r"(const\s+bool\s+TWO_SWITCH\s*=\s*)(true|false)", rf"\g<1>{'true' if two_switch else 'false'}"))
    if threshold is not None:
        changes.append((r"(const\s+int\s+THRESHOLD\s*=\s*)\d+", rf"\g<1>{int(threshold)}"))
    for pattern, replacement in changes:
        text, n = re.subn(pattern, replacement, text, count=1)
        if not n:
            raise ValueError(f"switch_ir.ino has no line matching {pattern!r}")
    return text
