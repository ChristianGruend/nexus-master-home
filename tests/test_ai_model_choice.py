from nexus.common import ai_layer


def test_ui_choice_beats_env_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_layer, "CONFIG_PATH", tmp_path / "ai_models.json")
    monkeypatch.setenv("NEXUS_MODEL", "qwen2.5:7b")
    assert ai_layer.load_config()["chat_model"] == "qwen2.5:7b"
    cfg = ai_layer.save_config({"chat_model": "qwen3.5:9b", "chat_model_source": "ui"})
    assert cfg["chat_model"] == "qwen3.5:9b"
    assert ai_layer.load_config()["chat_model"] == "qwen3.5:9b"
    st = ai_layer.status()
    assert st["chat_model_label"] == "Hermes (qwen3.5:9b)"
    assert st["chat_model_source"] == "ui"


def test_monitor_uses_ai_layer_model(monkeypatch):
    from nexus.computer import monitor
    from nexus.common import ollama_client

    class C:
        base_url = "http://x"
        model = None
        def ping(self):
            return {"reachable": True, "ok": True, "models": ["qwen3.5:9b", "qwen3.5:4b"]}
        def pick_model(self, preferred=None):
            return preferred or "qwen3.5:4b"

    monkeypatch.setattr(ollama_client, "OllamaClient", C)
    monkeypatch.setattr(ai_layer, "load_config", lambda: {"chat_model": "qwen3.5:9b"})
    o = monitor._ollama()
    assert o["preferred"] == "qwen3.5:9b"
    assert o["label"] == "Hermes (qwen3.5:9b)"
