# EMG calibration maths, tested on made-up recordings (no Arduino, no arduino-cli, no COM port).
import os
import random

import pytest

from tools import emg_calibrate as cal
from tools import serial_tools

HZ = 200


def recording(twitch_heights, rest_s=5.0, noise_sd=3.0, seed=1, level=500):
    """Rest, then per twitch: 2 s relaxed + a 1.5 s window with a 0.3 s burst of the given height.
    Returns (raw, rest range, twitch windows) like emg_calibrate.record()."""
    rng = random.Random(seed)
    noise = lambda: rng.gauss(0, noise_sd)
    raw = [level + noise() for _ in range(int(rest_s * HZ))]
    rest = (0, len(raw))
    windows = []
    for height in twitch_heights:
        raw += [level + noise() for _ in range(2 * HZ)]
        start = len(raw)
        window = [level + noise() for _ in range(int(1.5 * HZ))]
        for i in range(60, 120):                        # reaction time 0.3 s, burst 0.3 s
            window[i] += height
        raw += window
        windows.append((start, len(raw)))
    return [max(0, min(1023, round(v))) for v in raw], rest, windows


def test_replay_matches_the_sketch_on_simple_signals():
    flat = [500] * 400
    activation, events = cal.simulate_sketch(flat, 60)
    assert max(abs(a) for a in activation) < 1e-9 and events == []

    step = [500] * 300 + [700] * 100 + [500] * 300
    activation, events = cal.simulate_sketch(step, 60)
    # envelope after 1 sample at 700: 500 + 0.1*200 = 520; baseline barely moves
    assert activation[300] == pytest.approx(20 - 0.005 * 20, abs=1e-6)
    assert [s for _, s in events] == ["DOWN", "UP"]
    down = events[0][0]
    assert 300 < down < 320                             # crosses 60 within a few samples


def test_baseline_freezes_while_pressed_like_the_board():
    held = [500] * 300 + [700] * 600                     # a long, steady contraction
    activation, events = cal.simulate_sketch(held, 60)
    assert [s for _, s in events] == ["DOWN"]           # stays pressed: baseline doesn't creep up
    assert activation[-1] == pytest.approx(200, abs=10)


def test_good_recording_gives_a_threshold_between_noise_and_weakest_twitch():
    raw, rest, windows = recording([180, 150, 220, 160, 200])
    r = cal.analyze(raw, rest, windows)
    assert not r["overlap"]
    assert r["ceiling"] < r["threshold"] < r["weakest"]
    assert r["threshold"] == round((r["ceiling"] + r["weakest"]) / 2)
    assert r["weakest"] == min(r["peaks"]) and len(r["peaks"]) == 5
    assert r["peaks"].index(r["weakest"]) == 1          # the 150 burst
    assert r["false_downs_at_rest"] == 0
    assert r["missed_twitches"] == []
    assert r["margin"] == pytest.approx(r["weakest"] - r["ceiling"])
    assert 495 < r["rest_mean"] < 505 and 2 < r["rest_noise_sd"] < 4


def test_noise_and_twitch_overlap_is_reported_with_no_threshold():
    raw, rest, windows = recording([4, 3, 5, 4, 3], noise_sd=6)
    r = cal.analyze(raw, rest, windows)
    assert r["overlap"] is True and r["threshold"] is None
    text = "\n".join(cal.describe(r))
    assert "overlap" in text and "pads" in text


def test_one_weak_twitch_sets_the_threshold_and_a_small_margin_is_flagged():
    raw, rest, windows = recording([200, 200, 40, 200, 200], noise_sd=2)
    r = cal.analyze(raw, rest, windows)
    assert not r["overlap"] and r["missed_twitches"] == []
    assert r["threshold"] < r["peaks"][2]               # still catches the weak one
    tight = cal.analyze(*recording([18, 18, 18, 18, 18], noise_sd=2))
    assert not tight["overlap"] and tight["tight"] is True
    assert any("margin is small" in line for line in cal.describe(tight))


def test_a_missed_prompt_is_reported():
    raw, rest, windows = recording([200, 200, 0, 200, 200], noise_sd=2)
    r = cal.analyze(raw, rest, windows)
    assert r["overlap"] is True                        # the "twitch" that never happened is the weakest
    assert r["peaks"][2] < r["ceiling"] + 5


def test_too_short_rest_is_an_error():
    raw, _, windows = recording([200], rest_s=1.0)
    with pytest.raises(ValueError, match="too short"):
        cal.analyze(raw, (0, 1 * HZ), windows)


def test_writes_threshold_and_debug_into_the_real_sketch_text():
    with open(serial_tools.SWITCH_SKETCH, encoding="utf-8") as f:
        original = f.read()
    debug_on = original.replace("const bool DEBUG = false;", "const bool DEBUG = true;")
    new = cal.set_sketch_settings(debug_on, 87)
    assert "const int THRESHOLD = 87;" in new
    assert "const bool DEBUG = false;" in new
    # only those two lines change
    changed = [(a, b) for a, b in zip(original.splitlines(), new.splitlines()) if a != b]
    assert len(changed) == 1 and "THRESHOLD" in changed[0][0]
    with pytest.raises(ValueError):
        cal.set_sketch_settings("void loop() {}", 50)


@pytest.mark.parametrize("raw, expected", [
    (b"512\r\n", 512), (b"0\n", 0), (b"1023\r\n", 1023), (b"1024\r\n", None),
    (b"-3\r\n", None), (b"DOWN\r\n", None), (b"", None), (b"5\x0012", None),
])
def test_parse_sample(raw, expected):
    assert cal.parse_sample(raw) == expected


def test_listen_report_counts_false_downs_at_rest():
    events = [(3.2, "DOWN"), (3.4, "UP"), (11.0, "DOWN"), (11.3, "UP"), (14.0, "DOWN"), (14.2, "UP")]
    assert cal.listen_report(events, 10) == {"rest_downs": 1, "rest_ups": 1, "twitch_downs": 2, "twitch_ups": 2}


# The whole run with every hardware step faked

@pytest.fixture
def fake_hardware(monkeypatch, tmp_path):
    sketch = tmp_path / "switch_emg.ino"
    with open(serial_tools.SWITCH_SKETCH, encoding="utf-8") as f:
        sketch.write_text(f.read(), encoding="utf-8")
    uploads = []
    monkeypatch.setattr(serial_tools, "SWITCH_SKETCH", str(sketch))
    monkeypatch.setattr(serial_tools, "find_arduino_cli", lambda: "arduino-cli")

    def fake_upload(folder, port):            # remembers the sketch text instead of uploading
        name = os.path.basename(folder)
        with open(os.path.join(folder, name + ".ino"), encoding="utf-8") as f:
            uploads.append(f.read())
    monkeypatch.setattr(serial_tools, "upload_sketch", fake_upload)
    monkeypatch.setattr(cal, "listen", lambda port, seconds: {"rest_downs": 0, "rest_ups": 0,
                                                              "twitch_downs": 5, "twitch_ups": 5})
    return sketch, uploads


def test_full_run_writes_and_uploads_the_threshold(fake_hardware, monkeypatch, capsys):
    sketch, uploads = fake_hardware
    raw, rest, windows = recording([180, 150, 220, 160, 200])
    monkeypatch.setattr(cal, "record", lambda port, rest_s, n: (raw, rest, windows, 196.0))
    assert cal.main(["--port", "COM3"]) == 0
    threshold = cal.analyze(raw, rest, windows)["threshold"]
    assert f"const int THRESHOLD = {threshold};" in sketch.read_text(encoding="utf-8")
    assert len(uploads) == 2
    assert "analogRead(A0)" in uploads[0] and "Serial.begin(115200)" in uploads[0]   # raw stream first
    assert f"const int THRESHOLD = {threshold};" in uploads[1]                        # then the switch
    assert "Looks good" in capsys.readouterr().out


def test_overlap_changes_nothing(fake_hardware, monkeypatch):
    sketch, uploads = fake_hardware
    before = sketch.read_text(encoding="utf-8")
    monkeypatch.setattr(cal, "record", lambda port, rest_s, n: (*recording([3] * 5, noise_sd=6), 196.0))
    assert cal.main(["--port", "COM3"]) == 2
    assert sketch.read_text(encoding="utf-8") == before
    assert len(uploads) == 1                            # only the raw-stream sketch


def test_dry_run_changes_nothing(fake_hardware, monkeypatch):
    sketch, uploads = fake_hardware
    before = sketch.read_text(encoding="utf-8")
    monkeypatch.setattr(cal, "record", lambda port, rest_s, n: (*recording([200] * 5), 196.0))
    assert cal.main(["--port", "COM3", "--dry-run"]) == 0
    assert sketch.read_text(encoding="utf-8") == before and len(uploads) == 1
