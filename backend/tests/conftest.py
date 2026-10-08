# Shared fixtures: no real network calls, and a fresh data folder per test.
import os
import sys

import pytest

# let tests "import app" and "import predictor" from the backend folder
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as backend  # noqa: E402
from profiles import ProfileStore  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Safety net: if any test forgets to mock Colab or Gemini, fail loudly
    instead of sending a real request over the internet."""
    def refuse(*args, **kwargs):
        raise AssertionError(f"Test tried a REAL network call to {args[0] if args else kwargs}")
    monkeypatch.setattr(backend.requests, "post", refuse)
    monkeypatch.setattr(backend.requests, "get", refuse)
    # the Colab retry waits 1 second before trying again; skip that wait in tests
    monkeypatch.setattr(backend.time, "sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    """Every test gets its own empty data folder, so real profiles and
    recordings in backend/data are never touched."""
    fresh = ProfileStore(tmp_path / "data")
    monkeypatch.setattr(backend, "store", fresh)
    # the generation log goes in the temporary folder too
    monkeypatch.setattr(backend, "GENERATION_LOG", str(tmp_path / "data" / "generation_log.csv"))
    return fresh


@pytest.fixture(autouse=True)
def switch(monkeypatch):
    """Every test gets a fresh switch with NO serial port (a real COM port is never opened)
    and a fresh hub, so debounce times and websocket clients don't leak between tests."""
    from switch_bridge import SwitchBridge, SwitchHub
    fresh = SwitchBridge("")
    hub = SwitchHub()
    fresh.listeners.append(hub.publish)
    monkeypatch.setattr(backend, "switch", fresh)
    monkeypatch.setattr(backend, "switch_hub", hub)
    return fresh


@pytest.fixture(autouse=True)
def no_real_serial_port(monkeypatch):
    """Safety net: no test may open a real COM port (the Arduino could be plugged in)."""
    import serial

    def refuse(*args, **kwargs):
        raise AssertionError(f"Test tried to open a REAL serial port: {args or kwargs}")
    monkeypatch.setattr(serial, "Serial", refuse)
    monkeypatch.setattr(serial, "serial_for_url", refuse)
