from nexus.common import live_events as le


def test_emit_tail_mask(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "LIVE_FILE", tmp_path / "live.jsonl")
    monkeypatch.setenv("NEXUS_LIVE_FORCE", "1")
    rec = le.emit("Hermes", "tool", "token=abcdef123", target="Bridge", ok=True, data={"x": 1})
    assert rec and "abcdef123" not in rec["text"]
    ev = le.tail(5)
    assert ev[-1]["agent"] == "Hermes" and ev[-1]["target"] == "Bridge" and ev[-1]["ok"] is True


def test_no_write_and_no_window_in_pytest(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "LIVE_FILE", tmp_path / "live.jsonl")
    monkeypatch.delenv("NEXUS_LIVE_FORCE", raising=False)
    assert le.emit("NEXUS", "x", "y") is None and not (tmp_path / "live.jsonl").exists()
    assert le.ensure_monitor()["reason"] == "disabled"