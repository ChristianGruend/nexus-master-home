from nexus.autobuild import loop as L

def test_notes_text_contains_reset_note():
    t = L._notes_text({"reset_note": "rotate expand=True", "abnahme_text": "Masse stimmen"})
    assert "rotate expand=True" in t and "Masse stimmen" in t and "VERBINDLICH" in t
    assert L._notes_text({}) == ""

def test_function_prompt_includes_reset_note(monkeypatch, tmp_path=None):
    seen = {}
    def fake(prompt, *a, **k):
        seen["p"] = prompt
        raise RuntimeError("stop")
    monkeypatch.setattr(L, "_gpt", fake)
    item = {"id": "X", "title": "T", "goal": "G", "file": "nexus/studio/imgtools.py", "anchor": "def tool(",
            "reset_note": "MARKER_RESET_123", "acceptance": "tests/accept_studio_tools.py"}
    try:
        L.plan_function(item)
    except Exception:
        pass
    assert "MARKER_RESET_123" in seen.get("p", ""), "reset_note fehlt im Prompt"

def test_hermes_review_includes_reset_note(monkeypatch):
    from nexus.integrations import hermes_agent as H
    seen = {}
    monkeypatch.setattr(H, "health", lambda: {"reachable": True})
    monkeypatch.setattr(H, "ask", lambda task: (seen.setdefault("t", task), {"reply": "OK"})[1])
    item = {"title": "T", "reset_note": "MARKER_HERMES_456"}
    L.hermes_review(item, {"result": {"edits": [{"code": "def f():\n    return 1\n"}]}}, timeout=10)
    assert "MARKER_HERMES_456" in seen.get("t", "")
