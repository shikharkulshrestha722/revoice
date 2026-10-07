# Predictions must keep answering while Colab is busy generating speech.
import threading
import time

from fastapi.testclient import TestClient

import app as backend


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"audio_base64": "x"}


def test_predict_answers_while_speech_is_being_generated(store, monkeypatch):
    user = store.create("Asha", "cloned", consent_given=True)
    store.select(user["id"])
    store.add_clip(user["id"], "neutral", b"voice")
    monkeypatch.setattr(backend, "GEMINI_API_KEY", "")         # offline predictions

    colab_busy = threading.Event()
    let_colab_finish = threading.Event()

    def slow_colab(url, **kw):              # Colab "generating" until we say stop
        colab_busy.set()
        let_colab_finish.wait(10)
        return FakeResponse()

    monkeypatch.setattr(backend.requests, "post", slow_colab)

    # "with" = ONE shared event loop, exactly like the real uvicorn server
    with TestClient(backend.app) as client:
        speak_result = {}
        speaking = threading.Thread(target=lambda: speak_result.update(
            r=client.post("/speak", data={"text": "HELLO", "emotion": "neutral"})))
        speaking.start()
        assert colab_busy.wait(5)

        start = time.perf_counter()
        prediction = client.post("/predict", data={"text": "I "})
        waited = time.perf_counter() - start

        let_colab_finish.set()
        speaking.join(10)

    assert prediction.status_code == 200
    assert waited < 2, f"/predict waited {waited:.1f}s for speech generation to finish"
    assert speak_result["r"].status_code == 200
