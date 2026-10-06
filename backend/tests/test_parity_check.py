# The parity script must send EXACTLY the same reference bytes and text on both paths
# (otherwise its comparison would mean nothing). Colab is faked here.
import base64
import io
import sys
import wave

import pytest

import app as backend
import parity_check


def make_wav(seconds=3.0, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


@pytest.fixture
def fake_colab(monkeypatch):
    sent = []

    class Reply:
        status_code = 200

        def json(self):
            return {"audio_base64": "x", "synth_seconds": 1.0, "ref_text": "hello",
                    "metrics": {"similarity": 0.8}}

    def fake_post(url, json=None, **kw):
        sent.append(json)
        return Reply()
    monkeypatch.setattr(backend.requests, "post", fake_post)
    return sent


@pytest.mark.parametrize("name, data", [("upload.wav", make_wav()), ("Recording (2).m4a", b"m4a bytes \x00\xff")],
                         ids=["wav", "m4a"])
def test_both_paths_send_identical_bytes_and_text(tmp_path, monkeypatch, capsys, fake_colab, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    monkeypatch.setattr(sys, "argv", ["parity_check.py", str(path)])
    assert parity_check.main() == 0

    step_4b, website = fake_colab
    for payload in (step_4b, website):
        assert [base64.b64decode(c) for c in payload["speaker_wav_base64_list"]] == [data]
        assert payload["engine"] == "f5" and payload["emotion"] == "neutral"
    assert step_4b["text"] == website["text"] == parity_check.TEST_SENTENCE
    out = capsys.readouterr().out
    assert "identical reference bytes: YES; identical text: YES" in out


def test_profile_row_shows_what_a_real_user_sends(tmp_path, monkeypatch, capsys, fake_colab, store):
    p = store.create("Asha", "cloned", consent_given=True)
    store.add_clip(p["id"], "neutral", b"mic clip")               # Auto would use THIS, not the file
    path = tmp_path / "upload.wav"
    path.write_bytes(make_wav())
    monkeypatch.setattr(sys, "argv", ["parity_check.py", str(path), "--profile", p["id"]])
    assert parity_check.main() == 0
    assert [base64.b64decode(c) for c in fake_colab[2]["speaker_wav_base64_list"]] == [b"mic clip"]
    assert "sends DIFFERENT audio from this file: neutral/000001.webm" in capsys.readouterr().out
