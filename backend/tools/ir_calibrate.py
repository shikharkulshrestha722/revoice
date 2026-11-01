"""IR switch check and tuning for hardware/switch_ir (Arduino Uno, wired).

    cd backend
    py -3.10 tools/ir_calibrate.py                    # live NEAR/far + press counter (Ctrl+C to stop)
    py -3.10 tools/ir_calibrate.py --sensor analog    # TCRT5000: measure rest + presses -> THRESHOLD
    py -3.10 tools/ir_calibrate.py --save             # then write the settings into switch_ir.ino,
                                                      # upload it and check the real DOWN/UP lines
Options: --port COM3, --one-switch / --two-switch, --seconds 30 (live view length).

Digital modules (FC-51 / E18-D80NK): no threshold maths. The module's potentiometer sets the
distance: while the live view runs, turn it until the switch shows NEAR only when the finger is
RAISED (wristband) or the eyebrow/cheek moves (glasses), and "far" at rest. The press counter
uses the sketch's own rules (debounce, MIN_HOLD_MS, refractory), so it counts what ReVoice will get.

Analog modules (TCRT5000): rest is measured, then you press each switch 3 times; THRESHOLD =
halfway between the resting noise and the weakest press, checked on the recording.

Nothing else may have the COM port open (Serial Monitor, the backend with SWITCH_SERIAL_PORT set,
twitch_to_space.py). This tool never changes the EMG sketch.
"""

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from switch_bridge import parse_line                     # noqa: E402
from tools import serial_tools                           # noqa: E402
from tools.ir_logic import (PressDetector, analog_near, replay, set_sketch_settings,  # noqa: E402
                            sketch_settings)

STREAM_BAUD = 115200
SWITCH_BAUD = 9600
BASELINE_MS = 2000          # the sketch averages 2 s at reset; the recording does the same
PRESSES_PER_SWITCH = 3
SWITCH_NAMES = ("SELECT", "NEXT")


def raw_stream_sketch(sensor):
    """Temporary sketch: prints "select,next" every 5 ms (digital: 1 = near; analog: 0-1023)."""
    if sensor == "analog":
        read = ("analogRead(A0)", "analogRead(A1)")
        setup = ""
    else:
        read = ("(digitalRead(2) == LOW ? 1 : 0)", "(digitalRead(3) == LOW ? 1 : 0)")
        setup = " pinMode(2, INPUT_PULLUP); pinMode(3, INPUT_PULLUP);"
    return ("// ReVoice IR calibration: streams the sensors. Temporary; ir_calibrate.py replaces it.\n"
            f"void setup() {{ Serial.begin({STREAM_BAUD});{setup} }}\n"
            "void loop() {\n"
            f"  Serial.print({read[0]}); Serial.print(','); Serial.println({read[1]});\n"
            "  delay(5);\n"
            "}\n")


def parse_pair(raw):
    """b"1,0\\r\\n" -> (1, 0); anything else -> None."""
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    parts = (raw or "").strip().split(",")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


class LiveView:
    """Counts presses with the sketch's rules and makes the one-line status."""

    def __init__(self, two_switch, min_hold_ms, threshold=None, baselines=(0, 0)):
        self.count = 2 if two_switch else 1
        self.detectors = [PressDetector(min_hold_ms) for _ in range(self.count)]
        self.threshold = threshold              # None = digital
        self.baselines = baselines
        self.near = [False] * self.count

    def feed(self, t_ms, values):
        """One sample; returns the events, e.g. [("SELECT", "DOWN")]."""
        events = []
        for i in range(self.count):
            d = self.detectors[i]
            if self.threshold is None:
                near = bool(values[i])
            else:
                near = analog_near(values[i], self.baselines[i], self.threshold, d.reading)
            self.near[i] = near
            event = d.update(near, t_ms)
            if event:
                events.append((SWITCH_NAMES[i], event))
        return events

    def status(self):
        return "   ".join(f"{SWITCH_NAMES[i]}: {'NEAR' if self.near[i] else 'far '}  presses {self.detectors[i].presses}"
                           for i in range(self.count))


def analyze_switch(samples, baseline_end, rest_end, windows, min_hold_ms):
    """One analog channel. samples = [(t_ms, value)]; the first baseline_end samples give the
    resting value, baseline_end..rest_end are rest (noise), windows = [(start, end)] sample ranges
    of the press prompts. Returns a report with the suggested THRESHOLD, checked on the recording."""
    baseline = statistics.fmean(v for _, v in samples[:baseline_end])
    noise = max(abs(v - baseline) for _, v in samples[baseline_end:rest_end])
    peaks = [max((abs(v - baseline) for _, v in samples[a:b]), default=0.0) for a, b in windows]
    weakest = min(peaks) if peaks else 0.0
    report = {"baseline": baseline, "noise": noise, "peaks": peaks, "weakest": weakest,
              "overlap": weakest <= noise, "threshold": None, "false_at_rest": None,
              "missed": None, "usable": False}
    if report["overlap"]:
        return report
    threshold = int(round((noise + weakest) / 2))
    if not noise < threshold < weakest:
        report["overlap"] = True
        return report
    events = replay(samples[baseline_end:], threshold, baseline, min_hold_ms)
    downs = [t for t, e in events if e == "DOWN"]
    rest_t = (samples[baseline_end][0], samples[rest_end - 1][0])
    report["threshold"] = threshold
    report["false_at_rest"] = sum(rest_t[0] <= t <= rest_t[1] for t in downs)
    per_window = [sum(samples[a][0] <= t <= samples[b - 1][0] + 200 for t in downs) for a, b in windows]
    report["missed"] = [n + 1 for n, c in enumerate(per_window) if c == 0]
    report["usable"] = not report["false_at_rest"] and not report["missed"]
    return report


def describe(name, report):
    lines = [f"{name}: resting value {report['baseline']:.0f}, noise up to {report['noise']:.0f}, "
             f"presses " + ", ".join(f"{p:.0f}" for p in report["peaks"])]
    if report["overlap"]:
        lines.append(f"  PROBLEM: the weakest press ({report['weakest']:.0f}) is not above the resting noise. "
                     "Move the sensor closer to the finger/skin, shield it from sunlight, and try again.")
    else:
        lines.append(f"  Suggested THRESHOLD = {report['threshold']}"
                     + ("" if report["usable"] else
                        f"  (NOT reliable on the recording: {report['false_at_rest']} false at rest, "
                        f"missed press(es) {report['missed']})"))
    return lines


# Talking to the board

def read_for(conn, seconds, out, start, clock=time.monotonic):
    """Reads "select,next" samples for `seconds` into out as (ms since start, (select, next))."""
    end = clock() + seconds
    while clock() < end:
        pair = parse_pair(conn.readline())
        if pair is not None:
            out.append((int((clock() - start) * 1000), pair))


def record_analog(conn, two_switch):
    """Rest, then PRESSES_PER_SWITCH prompts per switch. Returns (samples, rest_end, windows per switch)."""
    samples, start = [], time.monotonic()
    print("\nKeep your fingers / face AWAY from the sensors (measuring rest, 4 s)...")
    read_for(conn, 4.0, samples, start)
    rest_end = len(samples)
    windows = []
    for i in range(2 if two_switch else 1):
        mine = []
        for n in range(1, PRESSES_PER_SWITCH + 1):
            print("   relax...")
            read_for(conn, 1.5, samples, start)
            print(f">>> PRESS {SWITCH_NAMES[i]} now and hold briefly ({n}/{PRESSES_PER_SWITCH}) <<<")
            a = len(samples)
            read_for(conn, 1.5, samples, start)
            mine.append((a, len(samples)))
        windows.append(mine)
    print("Done.")
    return samples, rest_end, windows


def live(conn, view, seconds):
    """Live status line until Ctrl+C (or `seconds`)."""
    start = time.monotonic()
    print("\nLive view (Ctrl+C to stop):")
    try:
        while seconds is None or time.monotonic() - start < seconds:
            pair = parse_pair(conn.readline())
            if pair is None:
                continue
            for name, event in view.feed(int((time.monotonic() - start) * 1000), pair):
                if event == "DOWN":
                    print(f"\r{name} press".ljust(70))
            print("\r" + view.status(), end="", flush=True)
    except KeyboardInterrupt:
        pass
    print()


def final_check(port, seconds=10.0):
    """Listens to the real sketch: prints its DOWN/UP and NEXT_DOWN/NEXT_UP lines."""
    conn = serial_tools.open_port(port, SWITCH_BAUD, timeout=0.1)
    counts = {}
    try:
        print("\nKeep AWAY from the sensors for 2 s (the board measures rest)...")
        time.sleep(2.5)
        conn.reset_input_buffer()
        print(f"Now press each switch a few times ({seconds:g} s):")
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            state = parse_line(conn.readline())
            if state:
                counts[state] = counts.get(state, 0) + 1
                print(f"  {time.monotonic() - start:5.1f} s  {state}")
    finally:
        conn.close()
    print("Counts:", counts or "nothing received (check the wiring, or press longer than MIN_HOLD_MS)")
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check and tune the IR switches (switch_ir.ino)")
    parser.add_argument("--port", help="e.g. COM3 (default: find the Arduino / CH340 port)")
    parser.add_argument("--sensor", choices=["digital", "analog"], help="default: SENSOR_TYPE in switch_ir.ino")
    count = parser.add_mutually_exclusive_group()
    count.add_argument("--one-switch", action="store_true", help="only SELECT (glasses, 1 sensor)")
    count.add_argument("--two-switch", action="store_true", help="SELECT + NEXT (wristband)")
    parser.add_argument("--seconds", type=float, help="live view length (default: until Ctrl+C)")
    parser.add_argument("--save", action="store_true",
                        help="write sensor type, number of switches (and THRESHOLD) into switch_ir.ino and upload it")
    args = parser.parse_args(argv)

    try:
        with open(serial_tools.IR_SKETCH, encoding="utf-8") as f:
            text = f.read()
        settings = sketch_settings(text)
        sensor = args.sensor or settings["sensor"]
        two = settings["two_switch"] if not (args.one_switch or args.two_switch) else args.two_switch
        port = args.port or serial_tools.choose_port()
        serial_tools.find_arduino_cli()
        print(f"{sensor} sensor(s), {'2 switches' if two else '1 switch'}, MIN_HOLD_MS = {settings['min_hold_ms']}")
        print(f"Uploading the temporary sensor-stream sketch to {port}...")
        serial_tools.upload_sketch(serial_tools.stage_sketch("ir_raw_stream", raw_stream_sketch(sensor)), port)

        conn = serial_tools.open_port(port, STREAM_BAUD, timeout=0.05)
        threshold = None
        try:
            time.sleep(2.0)                                   # the board resets when the port opens
            conn.reset_input_buffer()
            if sensor == "analog":
                samples, rest_end, windows = record_analog(conn, two)
                n_base = sum(t < BASELINE_MS for t, _ in samples)
                reports = []
                for i in range(2 if two else 1):
                    channel = [(t, v[i]) for t, v in samples]
                    report = analyze_switch(channel, n_base, rest_end, windows[i], settings["min_hold_ms"])
                    reports.append(report)
                    print("\n".join(describe(SWITCH_NAMES[i], report)))
                if all(r["usable"] for r in reports):
                    threshold = max(r["threshold"] for r in reports)   # one THRESHOLD for both
                    print(f"\nTHRESHOLD = {threshold} works for {'both switches' if two else 'the switch'}.")
                view = LiveView(two, settings["min_hold_ms"], threshold or settings["threshold"],
                                tuple(r["baseline"] for r in reports) + (0,))
            else:
                print("\nTurn each module's potentiometer until it shows NEAR only when the finger is raised")
                print("(or the eyebrow/cheek moves) and 'far' at rest; its own LED lights at the same moment.")
                view = LiveView(two, settings["min_hold_ms"])
            live(conn, view, args.seconds)
        finally:
            conn.close()

        if not args.save:
            print("\nNot saved. Run again with --save to write the settings into switch_ir.ino and upload it.")
            print("Until then the board runs the temporary stream sketch (upload switch_ir.ino before use).")
            return 0
        if sensor == "analog" and threshold is None:
            print("\nNOT saved: no reliable THRESHOLD. Fix the sensor position and run again.")
            return 2
        text = set_sketch_settings(text, sensor=sensor, two_switch=two, threshold=threshold)
        with open(serial_tools.IR_SKETCH, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        print(f"\nSaved into {serial_tools.IR_SKETCH}. Uploading it...")
        serial_tools.upload_sketch(serial_tools.IR_SKETCH_DIR, port)
        final_check(port)
        return 0
    except (serial_tools.PortError, ValueError) as e:
        print(f"ERROR: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped. If the stream sketch is still on the board, upload switch_ir.ino again.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
