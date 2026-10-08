# Single switch (Arduino EMG -> "DOWN"/"UP" over serial). pyserial is NEVER really used:
# every test passes a fake port, so no COM port is opened.
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app as backend
from switch_bridge import SwitchBridge, SwitchHub, parse_line, describe_error

client = TestClient(backend.app)


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeSerialError(Exception):
    """Stands in for serial.SerialException (same kind of message pyserial gives on Windows)."""


class FakePort:
    """Gives back the queued lines, then b"" (a read timeout), or raises to act as an unplug."""
    def __init__(self, lines, unplug_after=False):
        self.lines = list(lines)
        self.unplug_after = unplug_after
        self.closed = False

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        if self.unplug_after:
            raise FakeSerialError("ClearCommError failed (PermissionError(13, 'The device does not recognize the command.'))")
        time.sleep(0.005)
        return b""

    def close(self):
        self.closed = True


def wait_until(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def collect(bridge):
    events = []
    bridge.listeners.append(events.append)
    return events


# Line parsing

@pytest.mark.parametrize("raw, expected", [
    (b"DOWN\r\n", "DOWN"), (b"UP\n", "UP"), ("  down ", "DOWN"), ("up", "UP"),
    (b"", None), (b"\r\n", None), (b"EMG=512\r\n", None), (b"DOWNUP\r\n", None),
    (b"\xff\xfeDO", None), (b"Arduino ready\r\n", None), (None, None),
])
def test_parse_line_accepts_only_down_and_up(raw, expected):
    assert parse_line(raw) == expected


def test_junk_lines_reach_nobody():
    bridge = SwitchBridge("COM3", clock=FakeClock())
    events = collect(bridge)
    for junk in (b"hello\r\n", b"512\r\n", b"\x00\x01", b""):
        assert bridge.handle_line(junk) is None
    assert events == []
    assert bridge.status()["last_event"] is None


# Debounce

def test_a_second_down_within_300_ms_is_ignored_with_its_up():
    clock = FakeClock()
    bridge = SwitchBridge("COM3", clock=clock)
    events = collect(bridge)

    assert bridge.handle_line(b"DOWN\r\n")["state"] == "DOWN"
    clock.now += 0.05
    assert bridge.handle_line(b"UP\r\n")["state"] == "UP"
    clock.now += 0.10                                  # 150 ms after the first DOWN: a bounce
    assert bridge.handle_line(b"DOWN\r\n") is None
    clock.now += 0.02
    assert bridge.handle_line(b"UP\r\n") is None       # the bounce's UP goes too
    clock.now += 0.20                                  # 370 ms after the first DOWN: a real press
    assert bridge.handle_line(b"DOWN\r\n")["state"] == "DOWN"

    assert [e["state"] for e in events] == ["DOWN", "UP", "DOWN"]


def test_down_just_after_300_ms_counts():
    clock = FakeClock()
    bridge = SwitchBridge("COM3", clock=clock)
    bridge.handle_state("DOWN")
    clock.now += 0.301
    assert bridge.handle_state("DOWN") is not None


def test_event_updates_status():
    bridge = SwitchBridge("COM3", clock=FakeClock())
    event = bridge.handle_state("DOWN")
    status = bridge.status()
    assert status["last_event"] == "DOWN"
    assert status["last_event_time"] == event["t"]
    assert isinstance(event["t"], float)


def test_a_broken_listener_does_not_stop_the_others():
    bridge = SwitchBridge("COM3", clock=FakeClock())
    bridge.listeners.append(lambda e: 1 / 0)
    events = collect(bridge)
    bridge.handle_state("DOWN")
    assert len(events) == 1


# Reader thread: missing / busy / unplugged ports never crash anything

def test_no_port_means_disabled_and_no_thread():
    bridge = SwitchBridge("", open_port=lambda *a: pytest.fail("must not open a port"))
    bridge.start()
    assert bridge._thread is None
    assert bridge.status()["enabled"] is False
    bridge.stop()


def test_missing_port_keeps_retrying_without_crashing():
    attempts = []

    def missing(port, baud):
        attempts.append(port)
        raise FakeSerialError(f"could not open port '{port}': FileNotFoundError(2, 'The system cannot find the file specified.')")

    bridge = SwitchBridge("COM9", open_port=missing, retry_s=0.01)
    bridge.start()
    try:
        assert wait_until(lambda: len(attempts) >= 3)          # it keeps trying
        status = bridge.status()
        assert status["enabled"] is True and status["connected"] is False
        assert "COM9 not found" in status["error"]
        assert bridge._thread.is_alive()
    finally:
        bridge.stop()
    assert not bridge._thread.is_alive()


def test_busy_port_message_says_close_the_serial_monitor():
    error = FakeSerialError("could not open port 'COM3': PermissionError(13, 'Access is denied.', None, 5)")
    assert "Serial Monitor" in describe_error("COM3", error)


def test_reads_lines_then_survives_an_unplug_and_reconnects():
    ports = [FakePort([b"DOWN\r\n", b"noise\r\n", b"UP\r\n"], unplug_after=True),
             FakePort([b"DOWN\r\n"])]
    opened = []

    def open_port(port, baud):
        if not ports:
            raise FakeSerialError("could not open port 'COM3': FileNotFoundError(2)")
        opened.append(ports.pop(0))
        return opened[-1]

    clock = FakeClock()
    bridge = SwitchBridge("COM3", open_port=open_port, retry_s=0.01, clock=clock)
    events = collect(bridge)
    bridge.listeners.append(lambda e: setattr(clock, "now", clock.now + 1))   # presses 1 s apart
    bridge.start()
    try:
        assert wait_until(lambda: len(events) == 3)
        assert [e["state"] for e in events] == ["DOWN", "UP", "DOWN"]
        assert opened[0].closed                                  # the unplugged port was closed
        assert wait_until(lambda: bridge.status()["connected"])
    finally:
        bridge.stop()
    assert opened[1].closed                                      # stop() frees the COM port
    assert bridge.status()["connected"] is False


def test_lost_connection_message():
    assert "Lost the switch on COM3" in describe_error("COM3", FakeSerialError("x"), was_connected=True)


def test_server_startup_opens_the_port_once_and_shutdown_closes_it(monkeypatch):
    port = FakePort([])
    opened = []
    bridge = SwitchBridge("COM3", open_port=lambda p, b: opened.append(p) or port, retry_s=0.01)
    monkeypatch.setattr(backend, "switch", bridge)
    with TestClient(backend.app) as c:                           # runs the startup + shutdown code
        assert wait_until(lambda: bridge.status()["connected"])
        assert c.get("/switch/status").json()["connected"] is True
        c.websocket_connect("/ws/switch").close()                # a page connecting doesn't open it again
    assert opened == ["COM3"]
    assert port.closed


# Endpoints

def test_status_endpoint_without_hardware():
    data = client.get("/switch/status").json()
    assert data["enabled"] is False
    assert data["connected"] is False
    assert data["port"] == ""
    assert data["last_event"] is None
    assert data["clients"] == 0


def test_simulate_rejects_other_states():
    r = client.post("/switch/simulate", json={"state": "PRESS"})
    assert r.status_code == 400


def test_simulate_reaches_a_websocket_client():
    with client.websocket_connect("/ws/switch") as ws:
        r = client.post("/switch/simulate", json={"state": "down"})
        assert r.json()["accepted"] is True
        message = ws.receive_json()
    assert message["state"] == "DOWN"
    assert isinstance(message["t"], float)
    assert client.get("/switch/status").json()["last_event"] == "DOWN"


def test_simulate_is_debounced_like_the_real_switch():
    with client.websocket_connect("/ws/switch") as ws:
        assert client.post("/switch/simulate", json={"state": "DOWN"}).json()["accepted"] is True
        assert client.post("/switch/simulate", json={"state": "DOWN"}).json()["accepted"] is False
        assert client.post("/switch/simulate", json={"state": "UP"}).json()["accepted"] is False
        assert ws.receive_json()["state"] == "DOWN"


def test_every_connected_client_gets_every_event():
    with client.websocket_connect("/ws/switch") as a, client.websocket_connect("/ws/switch") as b:
        assert client.get("/switch/status").json()["clients"] == 2
        client.post("/switch/simulate", json={"state": "DOWN"})
        client.post("/switch/simulate", json={"state": "UP"})
        for ws in (a, b):
            assert [ws.receive_json()["state"], ws.receive_json()["state"]] == ["DOWN", "UP"]


def test_closed_pages_are_forgotten():
    with client.websocket_connect("/ws/switch"):
        pass
    assert wait_until(lambda: backend.switch_hub.client_count() == 0)


def test_events_from_the_reader_thread_reach_websocket_clients():
    """The real path: a background thread (not the event loop) hands the event over."""
    with client.websocket_connect("/ws/switch") as ws:
        t = threading.Thread(target=backend.switch.handle_line, args=(b"DOWN\r\n",))
        t.start()
        t.join()
        assert ws.receive_json()["state"] == "DOWN"


def test_hub_publish_with_no_clients_is_harmless():
    SwitchHub().publish({"state": "DOWN", "t": 0.0})
