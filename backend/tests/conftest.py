# Shared test setup.
# Run all tests from the backend folder with:   py -3.10 -m pytest
import os
import sys

import pytest

# let tests "import app" and "import predictor" from the backend folder
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as backend  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Safety net: if any test forgets to mock Colab or Gemini, fail loudly
    instead of sending a real request over the internet."""
    def refuse(*args, **kwargs):
        raise AssertionError(f"Test tried a REAL network call to {args[0] if args else kwargs}")
    monkeypatch.setattr(backend.requests, "post", refuse)


@pytest.fixture(autouse=True)
def empty_voice_bank():
    """Every test starts with an empty voice bank."""
    backend.voice_bank.clear()
    yield
    backend.voice_bank.clear()
