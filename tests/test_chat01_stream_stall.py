"""CHAT01 Abnahmetest: chat_stream darf nicht ewig am Inferenz-Lock haengen."""
import threading
import time

from nexus.common import ollama_client as oc


def test_chat_stream_busy_lock_gives_honest_error_fast(monkeypatch):
    c = oc.OllamaClient()
    c.lock_wait = 0.5  # Sekunden, die chat_stream maximal auf den Inferenz-Lock wartet
    monkeypatch.setattr(oc.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("darf nicht senden")))
    held = threading.Event(); release = threading.Event()

    def hold():
        with oc._INFERENCE_LOCK:
            held.set(); release.wait(5)

    t = threading.Thread(target=hold, daemon=True); t.start(); held.wait(2)
    try:
        t0 = time.time()
        evs = list(c.chat_stream([{"role": "user", "content": "hi"}], model="qwen3.5:9b"))
        dt = time.time() - t0
    finally:
        release.set(); t.join(2)
    assert dt < 3.0
    done = [e for e in evs if e.get("type") == "done"]
    assert done and done[-1]["success"] is False
    assert done[-1]["error"] == "ollama_busy"
