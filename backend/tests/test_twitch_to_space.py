# Twitch -> Space tool. Serial and pyautogui are always fakes: no port is opened, no key is pressed.
import sys
import types
from types import SimpleNamespace

import pytest
import serial

from tools import serial_tools, twitch_to_space
from tools.twitch_to_space import TwitchToSpace


class FakeClock:
    def __init__(self):
        self.now = 50.0

    def __call__(self):
        return self.now


def make_bridge(lockout=0.4):
    clock, presses, logs = FakeClock(), [], []
    bridge = TwitchToSpace(lockout=lockout, press=lambda: presses.append(clock.now),
                           clock=clock, log=logs.append)
    return bridge, clock, presses, logs


def test_down_presses_space_and_is_printed():
    bridge, clock, presses, logs = make_bridge()
    assert bridge.handle_line(b"DOWN\r\n") is True
    assert presses == [50.0]
    assert logs[-1].startswith("Twitch 1 -> Space")


def test_up_and_junk_lines_do_nothing():
    bridge, clock, presses, logs = make_bridge()
    for line in (b"UP\r\n", b"512,509.33,505.10\r\n", b"hello\r\n", b"", b"\xff\x00", b"DOWNDOWN\r\n"):
        assert bridge.handle_line(line) is False
    assert presses == [] and logs == []


def test_lockout_ignores_a_second_down_too_soon():
    bridge, clock, presses, logs = make_bridge(lockout=0.4)
    bridge.handle_line(b"DOWN\r\n")
    clock.now += 0.25
    bridge.handle_line(b"UP\r\n")
    assert bridge.handle_line(b"DOWN\r\n") is False          # 0.25 s: inside the lockout
    assert "ignored" in logs[-1]
    clock.now += 0.2                                         # 0.45 s after the accepted press
    assert bridge.handle_line(b"DOWN\r\n") is True
    assert len(presses) == 2 and bridge.presses == 2


def test_lockout_counts_from_the_last_ACCEPTED_press():
    bridge, clock, presses, _ = make_bridge(lockout=0.4)
    bridge.handle_state("DOWN")
    for _ in range(3):                                       # bounces every 0.15 s
        clock.now += 0.15
        bridge.handle_state("DOWN")
    assert len(presses) == 2                                 # 0.45 s after the first one


def test_default_press_uses_pyautogui_space(monkeypatch):
    pressed = []
    monkeypatch.setitem(sys.modules, "pyautogui", types.SimpleNamespace(press=pressed.append))
    twitch_to_space.press_space()
    assert pressed == ["space"]


class FakeConnection:
    def __init__(self, lines):
        self.lines = list(lines)
        self.closed = False
        self.flushed = False

    def reset_input_buffer(self):
        self.flushed = True

    def readline(self):
        if not self.lines:
            raise KeyboardInterrupt                          # ends the read loop, like Ctrl+C
        return self.lines.pop(0)

    def close(self):
        self.closed = True


def test_serial_loop_waits_for_the_arduino_then_presses(monkeypatch):
    conn = FakeConnection([b"DOWN\r\n", b"UP\r\n", b"noise\r\n"])
    monkeypatch.setattr(serial_tools, "open_port", lambda port, baud, timeout: conn)
    bridge, _, presses, _ = make_bridge()
    waits = []
    with pytest.raises(KeyboardInterrupt):
        twitch_to_space.run_serial("COM3", 9600, bridge, sleep=waits.append)
    assert waits == [2.0]                                    # Arduino reset + rest calibration
    assert conn.flushed and conn.closed
    assert len(presses) == 1


def test_fake_mode_enter_is_a_twitch():
    bridge, clock, presses, _ = make_bridge()
    answers = iter(["", "", "q"])

    def enter():
        clock.now += 1.0
        return next(answers)
    delays = []
    twitch_to_space.run_fake(bridge, delay=3.0, read=enter, sleep=delays.append)
    assert len(presses) == 2 and delays == [3.0, 3.0]


# Port problems: clear messages, no crash

def test_busy_port_message(monkeypatch):
    def busy(*args, **kwargs):
        raise serial.SerialException("could not open port 'COM3': PermissionError(13, 'Access is denied.', None, 5)")
    monkeypatch.setattr(serial, "Serial", busy)
    with pytest.raises(serial_tools.PortError) as err:
        serial_tools.open_port("COM3", 9600)
    message = str(err.value)
    assert "COM3 is busy" in message
    assert "Serial Monitor" in message and "SWITCH_SERIAL_PORT" in message


def test_missing_port_message(monkeypatch):
    def missing(*args, **kwargs):
        raise serial.SerialException("could not open port 'COM7': FileNotFoundError(2, 'The system cannot find the file specified.')")
    monkeypatch.setattr(serial, "Serial", missing)
    with pytest.raises(serial_tools.PortError, match="COM7 not found"):
        serial_tools.open_port("COM7", 9600)


def test_main_prints_the_error_and_exits_1(monkeypatch, capsys):
    def busy(port, baud, timeout):
        raise serial_tools.PortError("COM3 is busy: close the Serial Monitor")
    monkeypatch.setattr(serial_tools, "open_port", busy)
    assert twitch_to_space.main(["--port", "COM3"]) == 1
    assert "ERROR: COM3 is busy" in capsys.readouterr().out


# Port auto-detect

def port(device, description="", vid=None, manufacturer=""):
    return SimpleNamespace(device=device, description=description, vid=vid, manufacturer=manufacturer)


def test_finds_genuine_arduino_and_ch340_clone():
    ports = [port("COM1", "Communications Port"), port("COM3", "Arduino Uno (COM3)", 0x2341),
             port("COM5", "USB-SERIAL CH340 (COM5)", 0x1A86)]
    assert serial_tools.arduino_ports(ports) == ["COM3", "COM5"]


def test_one_arduino_is_chosen_without_asking():
    assert serial_tools.choose_port(ask=lambda q: pytest.fail("asked"), ports=[port("COM4", "Arduino Uno", 0x2341)]) == "COM4"


def test_no_arduino_asks_for_a_port_and_enter_quits():
    assert serial_tools.choose_port(ask=lambda q: "COM9", ports=[port("COM1", "Communications Port")]) == "COM9"
    with pytest.raises(serial_tools.PortError):
        serial_tools.choose_port(ask=lambda q: "", ports=[])


def test_several_arduinos_ask_which():
    ports = [port("COM3", "Arduino Uno", 0x2341), port("COM4", "Arduino Uno", 0x2341)]
    assert serial_tools.choose_port(ask=lambda q: "COM4", ports=ports) == "COM4"
