# Second switch (NEXT) for 2-switch step scanning: "NEXT_DOWN"/"NEXT_UP" lines. Fake ports and
# clocks only; no COM port is opened.
import pytest
from fastapi.testclient import TestClient

import app as backend
from switch_bridge import SwitchBridge, parse_line
from tests.test_switch import FakeClock, collect
from tools.twitch_to_space import TwitchToSpace

client = TestClient(backend.app)


@pytest.mark.parametrize("raw, state", [
    (b"NEXT_DOWN\r\n", "NEXT_DOWN"), (b"next_up\n", "NEXT_UP"), (b"DOWN\r\n", "DOWN"),
    (b"NEXT\r\n", None), (b"1,0\r\n", None)])
def test_next_lines_are_understood(raw, state):
    assert parse_line(raw) == state


def test_next_events_reach_the_listeners():
    bridge = SwitchBridge("", clock=FakeClock())
    events = collect(bridge)
    bridge.handle_line(b"NEXT_DOWN\r\n")
    bridge.handle_line(b"NEXT_UP\r\n")
    assert [e["state"] for e in events] == ["NEXT_DOWN", "NEXT_UP"]


def test_each_switch_is_debounced_on_its_own():
    clock = FakeClock()
    bridge = SwitchBridge("", clock=clock)
    events = collect(bridge)
    bridge.handle_state("DOWN")
    clock.now += 0.05
    bridge.handle_state("NEXT_DOWN")            # 50 ms later, but the OTHER switch: accepted
    clock.now += 0.05
    bridge.handle_state("NEXT_DOWN")            # same switch within 300 ms: a bounce
    bridge.handle_state("NEXT_UP")              # ...and its UP is dropped with it
    bridge.handle_state("UP")
    assert [e["state"] for e in events] == ["DOWN", "NEXT_DOWN", "UP"]


def test_a_dropped_next_bounce_does_not_eat_the_select_up():
    clock = FakeClock()
    bridge = SwitchBridge("", clock=clock)
    events = collect(bridge)
    bridge.handle_state("NEXT_DOWN")
    bridge.handle_state("NEXT_DOWN")            # bounce: its UP is owed by NEXT only
    bridge.handle_state("DOWN")
    bridge.handle_state("UP")
    assert [e["state"] for e in events] == ["NEXT_DOWN", "DOWN", "UP"]


def test_select_debounce_is_unchanged_for_the_emg_switch():
    clock = FakeClock()
    bridge = SwitchBridge("", clock=clock)
    events = collect(bridge)
    for state in ("DOWN", "UP"):
        bridge.handle_state(state)
    clock.now += 0.1
    bridge.handle_state("DOWN")                 # bounce
    bridge.handle_state("UP")
    clock.now += 0.3
    bridge.handle_state("DOWN")
    assert [e["state"] for e in events] == ["DOWN", "UP", "DOWN"]


def test_simulate_accepts_next_and_rejects_nonsense():
    r = client.post("/switch/simulate", json={"state": "next_down"})
    assert r.status_code == 200 and r.json()["event"]["state"] == "NEXT_DOWN"
    bad = client.post("/switch/simulate", json={"state": "LEFT"})
    assert bad.status_code == 400 and "NEXT_DOWN" in bad.json()["error"]


def test_twitch_to_space_presses_enter_for_next_with_its_own_lockout():
    clock = FakeClock()
    keys = []
    bridge = TwitchToSpace(press=lambda: keys.append("space"), press_next=lambda: keys.append("enter"),
                           clock=clock, log=lambda *_: None)
    assert bridge.handle_line(b"NEXT_DOWN\r\n") is True
    assert bridge.handle_line(b"DOWN\r\n") is True             # other switch: not locked out
    clock.now += 0.1
    assert bridge.handle_line(b"NEXT_DOWN\r\n") is False       # same switch, too soon
    assert bridge.handle_line(b"NEXT_UP\r\n") is False
    clock.now += 0.4
    assert bridge.handle_line(b"NEXT_DOWN\r\n") is True
    assert keys == ["enter", "space", "enter"]
    assert (bridge.presses, bridge.next_presses) == (1, 2)
