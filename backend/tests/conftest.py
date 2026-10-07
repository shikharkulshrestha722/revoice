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
