"""Finger twitch -> Space key. The simplest way to use the EMG switch with ReVoice.

Reads "DOWN"/"UP" from the Arduino and presses Space (pyautogui) on every DOWN, so ReVoice's
scanning mode sees an ordinary spacebar press. A second switch (hardware/switch_ir with
TWO_SWITCH) sends "NEXT_DOWN": that presses Enter (= NEXT in 2-switch step scanning). Leave SWITCH_SERIAL_PORT EMPTY in backend/.env
when using this: only one program can open the COM port.

    cd backend
    py -3.10 tools/twitch_to_space.py               # finds the Arduino port by itself
    py -3.10 tools/twitch_to_space.py --port COM3
    py -3.10 tools/twitch_to_space.py --fake        # no Arduino: Enter = a twitch

The ReVoice browser tab must be the focused window (Space goes to whatever window is in front).
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))    # backend/ for imports

from switch_bridge import parse_line                     # noqa: E402  same DOWN/UP rules as the backend
from tools import serial_tools                           # noqa: E402

ARDUINO_RESET_S = 2.0      # opening the port resets the Uno; its setup() then measures rest for 2 s


def press_space():
    import pyautogui                                     # imported here so tests never need a screen
    pyautogui.press("space")


def press_enter():
    import pyautogui
    pyautogui.press("enter")


class TwitchToSpace:
    """DOWN -> Space, except DOWNs inside the lockout after the last accepted one (muscle bounce).
    NEXT_DOWN -> Enter, with its own lockout (two switches never block each other)."""

    def __init__(self, lockout=0.4, press=press_space, clock=time.monotonic, log=print,
                 press_next=press_enter):
        self.lockout = lockout
        self.press = press
        self.press_next = press_next
        self.clock = clock
        self.log = log
        self.last_press = None
        self.last_next = None
        self.presses = 0
        self.next_presses = 0

    def handle_next(self):
        """Returns True if Enter was pressed."""
        now = self.clock()
        if self.last_next is not None and now - self.last_next < self.lockout:
            return False
        self.last_next = now
        self.press_next()
        self.next_presses += 1
        self.log(f"NEXT {self.next_presses} -> Enter  [{time.strftime('%H:%M:%S')}]")
        return True

    def handle_state(self, state):
        """Returns True if Space (DOWN) or Enter (NEXT_DOWN) was pressed."""
        if state == "NEXT_DOWN":
            return self.handle_next()
        if state != "DOWN":
            return False
        now = self.clock()
        if self.last_press is not None and now - self.last_press < self.lockout:
            self.log(f"  (ignored: {now - self.last_press:.2f} s after the last press)")
            return False
        self.last_press = now
        self.press()
        self.presses += 1
        self.log(f"Twitch {self.presses} -> Space  [{time.strftime('%H:%M:%S')}]")
        return True

    def handle_line(self, raw):
        state = parse_line(raw)          # junk, DEBUG numbers, start-up text -> None
        return self.handle_state(state) if state else False


def run_serial(port, baud, bridge, sleep=time.sleep):
    connection = serial_tools.open_port(port, baud, timeout=0.2)
    try:
        print(f"Opened {port}. Keep your hand relaxed: the Arduino is measuring rest...")
        sleep(ARDUINO_RESET_S)
        connection.reset_input_buffer()                  # drop anything from the reset
        print("Ready. Twitch to press Space. Keep the ReVoice tab focused. Ctrl+C to stop.")
        while True:
            line = connection.readline()
            if line:
                bridge.handle_line(line)
    finally:
        connection.close()


def run_fake(bridge, delay, read=input, sleep=time.sleep):
    """Enter = one twitch. With a delay you have time to click the ReVoice tab first."""
    print("Fake mode (no Arduino). Press Enter to simulate a twitch; type q + Enter to quit.")
    if delay:
        print(f"Space is pressed {delay:g} s after Enter, so click the ReVoice tab in that time.")
    print("Ready.")
    while True:
        try:
            text = read()
        except EOFError:
            return
        if text.strip().lower() == "q":
            return
        if delay:
            sleep(delay)
        bridge.handle_state("DOWN")


def main(argv=None):
    parser = argparse.ArgumentParser(description="EMG twitch (Arduino DOWN line) -> Space key")
    parser.add_argument("--port", help="e.g. COM3 (default: find the Arduino / CH340 port)")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--lockout", type=float, default=0.4,
                        help="seconds after a press during which another DOWN is ignored (default 0.4)")
    parser.add_argument("--fake", action="store_true", help="no Arduino: press Enter to simulate a twitch")
    parser.add_argument("--fake-delay", type=float, default=3.0,
                        help="--fake only: seconds between Enter and Space (default 3; 0 = at once)")
    args = parser.parse_args(argv)

    bridge = TwitchToSpace(lockout=args.lockout)
    try:
        if args.fake:
            run_fake(bridge, args.fake_delay)
        else:
            run_serial(args.port or serial_tools.choose_port(), args.baud, bridge)
    except serial_tools.PortError as e:
        print(f"ERROR: {e}")
        return 1
    except KeyboardInterrupt:
        pass
    print(f"Stopped after {bridge.presses} press(es).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
