# IR switches: the press rules (debounce, MIN_HOLD_MS, refractory) shared by both sketches and
# tools/ir_logic.py, the calibration maths, and the upload helper. No board, no COM port.
import pathlib
import re
from types import SimpleNamespace

import pytest

from switch_bridge import parse_line
from tools import ir_calibrate, ir_logic, serial_tools, switch_upload
from tools.ir_logic import PressDetector, analog_near, replay

UNO = pathlib.Path(serial_tools.IR_SKETCH).read_text(encoding="utf-8")
ESP32 = (pathlib.Path(serial_tools.ESP32_SKETCH_DIR) / "switch_esp32_ble.ino").read_text(encoding="utf-8")


def held(start, length, step=5):
    """Samples (t, near) every 5 ms: far until `start`, near for `length` ms, then far for 400 ms."""
    end = start + length
    return [(t, 1 if start <= t < end else 0) for t in range(0, end + 400, step)]


# Press rules

def test_a_brush_shorter_than_min_hold_is_ignored():
    assert replay(held(100, 100)) == []                      # 100 ms < 120 ms


def test_a_hold_gives_one_down_after_min_hold_and_one_up_after_release():
    events = replay(held(100, 150))
    assert [e for _, e in events] == ["DOWN", "UP"]
    assert events[0][0] == 100 + 120                         # timed from the real start of the press
    assert events[1][0] == 100 + 150 + 30                    # release + debounce


def test_holding_for_seconds_is_still_one_press():
    assert [e for _, e in replay(held(0, 3000))] == ["DOWN", "UP"]


def test_a_flicker_inside_a_hold_is_debounced_away():
    samples = [(t, 0 if 200 <= t < 215 else 1) for t in range(0, 600, 5)] + [(t, 0) for t in range(600, 900, 5)]
    assert [e for _, e in replay(samples)] == ["DOWN", "UP"]


def downs_for(*presses):
    """DOWN times for presses given as (start, end) in ms."""
    d = PressDetector()
    downs = []
    for t in range(0, 1500, 5):
        if d.update(any(a <= t < b for a, b in presses), t) == "DOWN":
            downs.append(t)
    return downs


def test_refractory_time_delays_a_second_press_that_comes_too_soon():
    # 2nd press starts 80 ms after the 1st ended and is still held when the 300 ms are over
    assert downs_for((0, 150), (230, 600), (900, 1100)) == [120, 120 + 300, 900 + 120]


def test_a_press_entirely_inside_the_refractory_time_is_dropped():
    assert downs_for((0, 150), (200, 400), (900, 1100)) == [120, 900 + 120]


def test_min_hold_is_a_setting():
    assert replay(held(0, 100), min_hold_ms=60) != []
    assert replay(held(0, 200), min_hold_ms=250) == []


def test_analog_hysteresis_keeps_a_wobbly_press_as_one():
    assert analog_near(600, 500, 100, was_near=False) is False      # change 100: not above 100
    assert analog_near(601, 500, 100, was_near=False) is True
    assert analog_near(560, 500, 100, was_near=True) is True        # still above half (50)
    assert analog_near(550, 500, 100, was_near=True) is False
    samples = [(t, 500) for t in range(0, 200, 5)] + [(t, 620 if t % 20 else 560) for t in range(200, 600, 5)] \
        + [(t, 500) for t in range(600, 900, 5)]
    assert [e for _, e in replay(samples, threshold=100, baseline=500)] == ["DOWN", "UP"]


# The sketches use exactly these rules and the switch protocol

@pytest.mark.parametrize("sketch", [UNO, ESP32], ids=["uno", "esp32"])
def test_sketches_have_the_same_timing_numbers_as_ir_logic(sketch):
    number = lambda name: int(re.search(rf"const\s+unsigned\s+long\s+{name}\s*=\s*(\d+)", sketch).group(1))
    assert number("DEBOUNCE_MS") == ir_logic.DEBOUNCE_MS
    assert number("REFRACTORY_MS") == ir_logic.REFRACTORY_MS
    assert number("MIN_HOLD_MS") == ir_logic.MIN_HOLD_MS == 120
    assert "s.activeSince = s.readingSince" in sketch               # hold timed from the real start
    assert "bool held = s.active && s.reading &&" in sketch            # and still near right now


@pytest.mark.parametrize("sketch", [UNO, ESP32], ids=["uno", "esp32"])
def test_sketches_print_lines_the_backend_understands(sketch):
    for text in ("DOWN", "UP", "NEXT_DOWN", "NEXT_UP"):
        assert f'"{text}"' in sketch
        assert parse_line(text.encode() + b"\r\n") == text
    assert "Serial.begin(9600)" in sketch
    assert re.search(r"const\s+bool\s+TWO_SWITCH\s*=\s*(true|false)", sketch)
    assert re.search(r"const\s+bool\s+DEBUG\s*=\s*false", sketch)


def test_uno_sketch_pins_and_hysteresis():
    assert "SELECT_PIN = 2, NEXT_PIN = 3" in UNO and "INPUT_PULLUP" in UNO
    assert "SELECT_ANALOG = A0, NEXT_ANALOG = A1" in UNO
    assert "const int RELEASE = THRESHOLD / 2;" in UNO


def test_esp32_sketch_keyboard_pins_and_modes():
    assert 'BleKeyboard bleKeyboard("ReVoice Switch"' in ESP32
    assert "{\"DOWN\", \"UP\", ' '}" in ESP32 and '"NEXT_DOWN", "NEXT_UP", KEY_RETURN' in ESP32   # Space / Enter
    assert "if (bleKeyboard.isConnected()) bleKeyboard.write(s.key)" in ESP32                      # only when connected
    for pins in ("SELECT_TOUCH = 4, NEXT_TOUCH = 15", "SELECT_DIGITAL = 18, NEXT_DIGITAL = 19",
                 "SELECT_ANALOG = 34, NEXT_ANALOG = 35"):
        assert pins in ESP32
    for mode in ("TOUCH_TEST", "DIGITAL_PROXIMITY", "ANALOG_TCRT5000"):
        assert f"#define {mode}" in ESP32


# Settings block of switch_ir.ino

def test_settings_are_read_and_rewritten_without_touching_the_rest():
    s = ir_logic.sketch_settings(UNO)
    assert s["min_hold_ms"] == 120 and s["sensor"] in ("digital", "analog")
    new = ir_logic.set_sketch_settings(UNO, sensor="analog", two_switch=False, threshold=87)
    assert ir_logic.sketch_settings(new) == {"sensor": "analog", "two_switch": False,
                                             "min_hold_ms": 120, "threshold": 87}
    assert new.replace("ANALOG_TCRT5000;", "X").count("\n") == UNO.count("\n")
    assert "#define ANALOG_TCRT5000   2" in new                     # the #define lines are untouched


# Calibration tool (pure parts)

def test_stream_sketch_and_pairs():
    digital = ir_calibrate.raw_stream_sketch("digital")
    assert "digitalRead(2) == LOW ? 1 : 0" in digital and "INPUT_PULLUP" in digital
    assert "analogRead(A0)" in ir_calibrate.raw_stream_sketch("analog")
    assert ir_calibrate.parse_pair(b"1,0\r\n") == (1, 0)
    assert ir_calibrate.parse_pair(b"512,498\n") == (512, 498)
    for junk in (b"", b"DOWN\r\n", b"1,2,3\n", b"-1,4\n"):
        assert ir_calibrate.parse_pair(junk) is None


def test_live_view_counts_presses_like_the_board():
    view = ir_calibrate.LiveView(two_switch=True, min_hold_ms=120)
    events = []
    for t in range(0, 1000, 5):
        select = 1 if 100 <= t < 300 else 0
        nxt = 1 if 500 <= t < 560 else 0             # 60 ms brush: not a press
        events += view.feed(t, (select, nxt))
    assert events == [("SELECT", "DOWN"), ("SELECT", "UP")]
    assert view.status().startswith("SELECT: far   presses 1")
    assert "NEXT: far   presses 0" in view.status()
    assert "NEXT" not in ir_calibrate.LiveView(two_switch=False, min_hold_ms=120).status()


def analog_recording(press_level, noise=3):
    """2 s baseline + 2 s rest at ~500, then 3 presses (1.5 s relax, 1.5 s press) at press_level."""
    samples, t = [], 0
    for _ in range(800):                                  # 4 s of rest
        samples.append((t, 500 + (noise if t % 10 else -noise))); t += 5
    windows = []
    for _ in range(3):
        for _ in range(300):
            samples.append((t, 500)); t += 5
        start = len(samples)
        for _ in range(300):
            samples.append((t, press_level)); t += 5
        windows.append((start, len(samples)))
    return samples, windows


def test_analog_threshold_is_halfway_and_checked_on_the_recording():
    samples, windows = analog_recording(press_level=700)
    report = ir_calibrate.analyze_switch(samples, 400, 800, windows, 120)
    assert report["noise"] == pytest.approx(3, abs=0.5)
    assert report["threshold"] == round((report["noise"] + 200) / 2)
    assert report["usable"] and report["false_at_rest"] == 0 and report["missed"] == []


def test_analog_overlap_is_reported_not_guessed():
    samples, windows = analog_recording(press_level=502, noise=5)
    report = ir_calibrate.analyze_switch(samples, 400, 800, windows, 120)
    assert report["overlap"] and report["threshold"] is None
    assert "PROBLEM" in "\n".join(ir_calibrate.describe("SELECT", report))


# Upload helper and arduino-cli arguments

def fake_cli(outputs):
    calls = []

    def run(args, capture_output=True, text=True):
        calls.append(args[1:])
        key = " ".join(args[1:3])
        return SimpleNamespace(returncode=0, stdout=outputs.get(key, "ok"), stderr="")
    return run, calls


def test_emg_compile_arguments_are_unchanged(monkeypatch):
    monkeypatch.setattr(serial_tools, "find_arduino_cli", lambda: "arduino-cli")
    run, calls = fake_cli({})
    serial_tools.compile_sketch("folder", run)
    assert calls == [["compile", "--fqbn", "arduino:avr:uno", "folder"]]


def test_esp32_builds_with_the_nimble_flag(monkeypatch):
    monkeypatch.setattr(serial_tools, "find_arduino_cli", lambda: "arduino-cli")
    run, calls = fake_cli({"lib list": "ESP32 BLE Keyboard 0.3.2\nNimBLE-Arduino 1.4.3", "core list": "esp32:esp32 3.3.12"})
    assert switch_upload.main(["--board", "esp32", "--compile-only"], run=run) == 0
    assert calls[-1] == ["compile", "--fqbn", "esp32:esp32:esp32", "--build-property",
                         "compiler.cpp.extra_flags=-DUSE_NIMBLE", serial_tools.ESP32_SKETCH_DIR]


def test_uno_upload_uses_the_ir_sketch_folder(monkeypatch):
    monkeypatch.setattr(serial_tools, "find_arduino_cli", lambda: "arduino-cli")
    run, calls = fake_cli({})
    assert switch_upload.main(["--board", "uno", "--port", "COM9"], run=run) == 0
    assert calls == [["compile", "--fqbn", "arduino:avr:uno", serial_tools.IR_SKETCH_DIR],
                     ["upload", "-p", "COM9", "--fqbn", "arduino:avr:uno", serial_tools.IR_SKETCH_DIR]]


def test_missing_esp32_libraries_are_listed_with_install_commands(monkeypatch, capsys):
    monkeypatch.setattr(serial_tools, "find_arduino_cli", lambda: "arduino-cli")
    run, calls = fake_cli({"lib list": "No libraries installed.", "core list": "arduino:avr 1.8.8"})
    assert switch_upload.main(["--board", "esp32", "--compile-only"], run=run) == 1
    out = capsys.readouterr().out
    assert "core install esp32:esp32" in out and "NimBLE-Arduino@1.4.3" in out and "T-vK" in out
    assert not any(c[0] == "compile" for c in calls)
