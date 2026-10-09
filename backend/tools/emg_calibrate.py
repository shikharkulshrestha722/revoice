"""EMG switch calibration: measures YOUR rest noise and twitches, then sets THRESHOLD.

    cd backend
    py -3.10 tools/emg_calibrate.py               # finds the Arduino port by itself
    py -3.10 tools/emg_calibrate.py --port COM3 --dry-run    # measure + suggest, change nothing
    py -3.10 tools/emg_calibrate.py --port COM3 --check-only # upload switch_emg.ino as it is + 20 s check

Steps:
 1. Uploads a temporary sketch that streams raw A0 values (~200 per second, the same loop
    timing as switch_emg.ino).
 2. "Relax 5 s", then "TWITCH NOW" 5 times.
 3. Replays switch_emg.ino's own maths (envelope, slowly-following baseline) on the recording,
    so the numbers are in the sketch's units (with the RELEASE = THRESHOLD/2 hysteresis and the
    400 ms refractory time, so one twitch = one DOWN). THRESHOLD = halfway between the highest resting
    value (noise ceiling) and the weakest twitch. Overlap = pads or wiring problem: stops.
 4. Writes THRESHOLD (and DEBUG = false) into hardware/switch_emg.ino and uploads it.
 5. Listens 20 s (10 s rest, 10 s twitching) and reports DOWN/UP counts and false DOWNs.

Nothing else may have the COM port open (Serial Monitor, the backend with SWITCH_SERIAL_PORT,
twitch_to_space.py).
"""

import argparse
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from switch_bridge import parse_line          # noqa: E402
from tools import serial_tools                # noqa: E402

# Same numbers as switch_emg.ino, so the replay matches the board exactly.
ENVELOPE_ALPHA = 0.1
BASELINE_ALPHA = 0.005
INIT_SAMPLES = 200            # setup() averages 200 readings for the first baseline
STREAM_BAUD = 115200          # 200 numbers/s need more than 9600 baud
SWITCH_BAUD = 9600
MIN_SAMPLE_HZ = 150           # below this the replay no longer matches the sketch's timing
REFRACTORY_MS = 400           # no new DOWN within 400 ms of the previous DOWN
SAMPLE_HZ = 195               # loop speed (analogRead + delay(5)); record() measures the real one
TIGHT_MARGIN = 20             # a margin smaller than this works, but only just

# Temporary sketch: the same loop timing as switch_emg.ino (analogRead + delay(5)).
RAW_STREAM_SKETCH = """// ReVoice calibration: streams raw A0 values. Temporary; emg_calibrate.py replaces it.
void setup() { Serial.begin(115200); }
void loop() {
  Serial.println(analogRead(A0));
  delay(5);
}
"""


# Maths (pure, tested with made-up recordings)

def release_level(threshold):
    """RELEASE = THRESHOLD / 2 in the sketch (whole-number division, as C++ does for ints)."""
    return threshold // 2 if isinstance(threshold, int) else threshold / 2


def simulate_sketch(raw, threshold, init_samples=INIT_SAMPLES, hz=SAMPLE_HZ):
    """Runs switch_emg.ino's loop on recorded values. Returns (activation per sample, events),
    activation = envelope - baseline (what the sketch compares with THRESHOLD),
    events = [(sample index, "DOWN"|"UP")]. Same rules as the board:
    - DOWN when level > THRESHOLD, but not within REFRACTORY_MS of the previous DOWN
    - UP only when level < RELEASE (= THRESHOLD / 2), so a wobbly twitch gives one DOWN
    - the baseline stops following while pressed."""
    if not raw:
        return [], []
    first = raw[:init_samples]
    baseline = envelope = sum(first) / len(first)
    release = release_level(threshold)
    refractory_samples = REFRACTORY_MS / 1000 * hz
    pressed = False
    last_down = None
    activation, events = [], []
    for i, value in enumerate(raw):
        envelope = envelope * (1 - ENVELOPE_ALPHA) + value * ENVELOPE_ALPHA
        if not pressed:
            baseline = baseline * (1 - BASELINE_ALPHA) + envelope * BASELINE_ALPHA
        level = envelope - baseline
        activation.append(level)
        rested = last_down is None or i - last_down >= refractory_samples
        if not pressed and level > threshold and rested:
            pressed = True
            last_down = i
            events.append((i, "DOWN"))
        elif pressed and level < release:
            pressed = False
            events.append((i, "UP"))
    return activation, events


def analyze(raw, rest, twitches, init_samples=INIT_SAMPLES, hz=SAMPLE_HZ):
    """rest = (start, end) sample range of the relaxed part (it must come first: the sketch
    measures its baseline at rest); twitches = [(start, end), ...] one window per prompt."""
    rest_start, rest_end = rest
    settled = max(rest_start + init_samples, rest_start)
    if rest_end - settled < 50:
        raise ValueError("Rest recording too short (need a few seconds after the first 200 samples).")
    rest_raw = raw[settled:rest_end]

    # 1) noise ceiling: the highest level at rest (nothing pressed, baseline follows freely)
    free, _ = simulate_sketch(raw, float("inf"), init_samples, hz)
    ceiling = max(free[settled:rest_end])

    # 2) twitch peaks: baseline freezes as soon as the level leaves the noise, like a press would
    held, _ = simulate_sketch(raw, ceiling, init_samples, hz)
    peaks = [max(held[a:b]) if b > a else 0.0 for a, b in twitches]
    weakest = min(peaks) if peaks else 0.0

    report = {
        "rest_mean": statistics.fmean(rest_raw),
        "rest_noise_sd": statistics.pstdev(rest_raw),
        "ceiling": ceiling,
        "peaks": peaks,
        "weakest": weakest,
        "margin": weakest - ceiling,
        "overlap": weakest <= ceiling,
        "threshold": None,
        "false_downs_at_rest": None,
        "missed_twitches": None,
        "double_twitches": None,    # prompts that gave more than one DOWN (chatter)
        "tight": False,
        "usable": False,        # True only if the replay at THRESHOLD is perfect (safe to write)
    }
    if report["overlap"]:
        return report

    # 3) halfway between (the sketch compares with a whole number: level > THRESHOLD) ...
    threshold = int(round((ceiling + weakest) / 2))
    if not ceiling < threshold < weakest:
        report["overlap"] = True                         # no whole number fits between them
        return report
    # ... then check it on the recording with the sketch's exact behaviour
    _, events = simulate_sketch(raw, threshold, init_samples, hz)
    downs = [i for i, state in events if state == "DOWN"]
    per_window = [sum(a <= i < b for i in downs) for a, b in twitches]
    report["threshold"] = threshold
    report["false_downs_at_rest"] = sum(settled <= i < rest_end for i in downs)
    report["missed_twitches"] = [n + 1 for n, count in enumerate(per_window) if count == 0]
    report["double_twitches"] = [n + 1 for n, count in enumerate(per_window) if count > 1]
    report["tight"] = report["margin"] < TIGHT_MARGIN
    report["usable"] = (not report["false_downs_at_rest"] and not report["missed_twitches"]
                        and not report["double_twitches"])
    return report


def describe(report):
    """The report as lines for the terminal."""
    lines = [
        f"Rest:     mean {report['rest_mean']:.1f}, noise (sd) {report['rest_noise_sd']:.1f} (raw 0-1023 units)",
        f"Rest noise ceiling (highest level at rest): {report['ceiling']:.1f}",
        "Twitch peaks: " + ", ".join(f"{p:.1f}" for p in report["peaks"]),
        f"Weakest twitch: {report['weakest']:.1f}   margin above the noise: {report['margin']:.1f}",
    ]
    if report["overlap"]:
        lines += [
            "",
            "PROBLEM: the weakest twitch is not clearly above the resting noise - they overlap.",
            "No threshold can separate them. Check: pads firmly on clean skin, the two signal pads",
            "along the muscle a few cm apart, the reference pad on bone (elbow), cables not",
            "touching, the board powered from 5V. Then run this again.",
        ]
        return lines
    t = report["threshold"]
    lines.append(f"Suggested THRESHOLD = {t}  ({t - report['ceiling']:.1f} above the noise, "
                 f"{report['weakest'] - t:.1f} below the weakest twitch)")
    if report["false_downs_at_rest"]:
        lines.append(f"WARNING: on the recording this still triggers {report['false_downs_at_rest']}x at rest.")
    if report["missed_twitches"]:
        lines.append(f"WARNING: twitch(es) {report['missed_twitches']} would be missed on the recording.")
    if report["double_twitches"]:
        lines.append(f"WARNING: twitch(es) {report['double_twitches']} would fire more than one DOWN.")
    if not report["usable"]:
        lines.append("Not reliable enough to use: twitch firmly, and only on the prompts; "
                     "check the pads; then run this again.")
    if report["tight"]:
        lines.append("Note: the margin is small. Firmer twitches or better pad contact will make it more reliable.")
    return lines


def set_sketch_settings(text, threshold, debug=False):
    """Rewrites the THRESHOLD and DEBUG lines at the top of switch_emg.ino (comments kept)."""
    text, n1 = re.subn(r"(const\s+int\s+THRESHOLD\s*=\s*)\d+", rf"\g<1>{int(threshold)}", text, count=1)
    text, n2 = re.subn(r"(const\s+bool\s+DEBUG\s*=\s*)(true|false)", rf"\g<1>{'true' if debug else 'false'}",
                       text, count=1)
    if not (n1 and n2):
        raise ValueError("switch_emg.ino has no 'const int THRESHOLD = ...;' / 'const bool DEBUG = ...;' line.")
    return text


def parse_sample(raw):
    """b"512\\r\\n" -> 512; anything that isn't a 0-1023 reading -> None."""
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    text = (raw or "").strip()
    if not text.isdigit():
        return None
    value = int(text)
    return value if value <= 1023 else None


def listen_report(events, rest_seconds):
    """events = [(seconds since start, "DOWN"|"UP")] from the final check."""
    rest = [s for t, s in events if t < rest_seconds]
    active = [s for t, s in events if t >= rest_seconds]
    return {"rest_downs": rest.count("DOWN"), "rest_ups": rest.count("UP"),
            "twitch_downs": active.count("DOWN"), "twitch_ups": active.count("UP")}


# Talking to the board

def read_samples(conn, seconds, samples, clock=time.monotonic):
    end = clock() + seconds
    while clock() < end:
        value = parse_sample(conn.readline())
        if value is not None:
            samples.append(value)


def record(port, rest_s, n_twitches, sleep=time.sleep):
    """Guided recording. Returns (raw samples, rest range, twitch windows, samples per second)."""
    conn = serial_tools.open_port(port, STREAM_BAUD, timeout=0.05)
    samples = []
    try:
        sleep(2.0)                                  # the board resets when the port opens
        conn.reset_input_buffer()
        started = time.monotonic()
        print(f"\nRELAX your hand completely for {rest_s:g} s...")
        read_samples(conn, rest_s, samples)
        rest = (0, len(samples))
        windows = []
        for n in range(1, n_twitches + 1):
            print("   relax... get ready")
            read_samples(conn, 2.0, samples)
            print(f">>> TWITCH your finger NOW ({n}/{n_twitches}) <<<")
            start = len(samples)
            read_samples(conn, 1.5, samples)
            windows.append((start, len(samples)))
        rate = len(samples) / (time.monotonic() - started)
        print("Done. Relax.")
    finally:
        conn.close()
    return samples, rest, windows, rate


def listen(port, seconds, sleep=time.sleep):
    """Final check with the real sketch: first half at rest, second half twitching."""
    conn = serial_tools.open_port(port, SWITCH_BAUD, timeout=0.1)
    events = []
    try:
        print("\nKeep RELAXED - the Arduino is measuring your resting level...")
        sleep(2.5)
        conn.reset_input_buffer()
        half = seconds / 2
        print(f"CHECK 1/2: stay relaxed for {half:g} s (any DOWN now is a false trigger)")
        start = time.monotonic()
        told = False
        while (elapsed := time.monotonic() - start) < seconds:
            if not told and elapsed >= half:
                print(f"CHECK 2/2: twitch about 5 times in the next {half:g} s")
                told = True
            state = parse_line(conn.readline())
            if state:
                events.append((elapsed, state))
                print(f"  {elapsed:5.1f} s  {state}")
    finally:
        conn.close()
    return listen_report(events, half)


def current_threshold(text):
    """"THRESHOLD = 144" from the sketch text (for messages)."""
    match = re.search(r"const\s+int\s+THRESHOLD\s*=\s*(\d+)", text)
    return f"THRESHOLD = {match.group(1)}" if match else "THRESHOLD not found"


def final_check(port, text, seconds):
    """Uploads the switch sketch, listens, prints the DOWN/UP counts. Returns the exit code."""
    print("Uploading switch_emg.ino - keep your hand RELAXED...")
    serial_tools.upload_sketch(serial_tools.stage_sketch("switch_emg", text), port)
    result = listen(port, seconds)
    print(f"\nAt rest:   {result['rest_downs']} DOWN, {result['rest_ups']} UP   (should be 0)")
    print(f"Twitching: {result['twitch_downs']} DOWN, {result['twitch_ups']} UP   (one DOWN per twitch)")
    if result["rest_downs"]:
        print("False triggers at rest: run the calibration again with firmer pads, or raise THRESHOLD a little.")
    elif not result["twitch_downs"]:
        print("No twitches detected: twitch more firmly, check pad placement, or rerun the calibration.")
    else:
        print("Looks good. Commit the new THRESHOLD in hardware/switch_emg.ino if you want to keep it.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Calibrate the EMG switch threshold")
    parser.add_argument("--port", help="e.g. COM3 (default: find the Arduino / CH340 port)")
    parser.add_argument("--rest", type=float, default=5.0, help="seconds of rest to record (default 5)")
    parser.add_argument("--twitches", type=int, default=5, help="number of twitch prompts (default 5)")
    parser.add_argument("--listen", type=float, default=20.0, help="final check length in seconds (default 20)")
    parser.add_argument("--dry-run", action="store_true",
                        help="measure and suggest only: don't change or upload switch_emg.ino")
    parser.add_argument("--check-only", action="store_true",
                        help="no recording: upload switch_emg.ino with its current THRESHOLD, run only the final check")
    args = parser.parse_args(argv)

    try:
        port = args.port or serial_tools.choose_port()
        serial_tools.find_arduino_cli()
        if args.check_only:
            with open(serial_tools.SWITCH_SKETCH, encoding="utf-8") as f:
                text = f.read()
            print(f"--check-only: no recording, using switch_emg.ino as it is ({current_threshold(text)}).")
            return final_check(port, text, args.listen)
        print(f"Uploading the temporary raw-signal sketch to {port}...")
        serial_tools.upload_sketch(serial_tools.stage_sketch("emg_raw_stream", RAW_STREAM_SKETCH), port)

        raw, rest, windows, rate = record(port, args.rest, args.twitches)
        print(f"\nRecorded {len(raw)} samples ({rate:.0f} per second).")
        if rate < MIN_SAMPLE_HZ:
            print(f"WARNING: fewer than {MIN_SAMPLE_HZ}/s - the numbers may not match the real sketch.")
        report = analyze(raw, rest, windows, hz=rate)
        print("\n".join(describe(report)))

        if not report["usable"]:
            print("\nswitch_emg.ino was NOT changed. Re-upload it later with your old THRESHOLD, or fix the pads and rerun.")
            return 2
        if args.dry_run:
            print("\n--dry-run: switch_emg.ino not changed. The board still runs the raw-signal sketch;")
            print("run this again without --dry-run (or upload switch_emg.ino) before using the switch.")
            return 0

        with open(serial_tools.SWITCH_SKETCH, encoding="utf-8") as f:
            text = set_sketch_settings(f.read(), report["threshold"], debug=False)
        with open(serial_tools.SWITCH_SKETCH, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        print(f"\nWrote THRESHOLD = {report['threshold']}, DEBUG = false into {serial_tools.SWITCH_SKETCH}")
        return final_check(port, text, args.listen)
    except (serial_tools.PortError, ValueError) as e:
        print(f"ERROR: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped. If the raw-signal sketch is still on the board, upload switch_emg.ino again.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
