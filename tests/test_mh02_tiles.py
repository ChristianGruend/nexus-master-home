import json
from datetime import datetime
from nexus import nexus_os
from nexus.chat import engine


def test_last_real_chat_online(tmp_path, monkeypatch):
    f = tmp_path / "h.json"
    f.write_text(json.dumps({"sessions": {"a": {"messages": [
        {"role": "user", "content": "hi", "ts": datetime.now().isoformat()},
        {"role": "assistant", "content": "hallo", "ts": datetime.now().isoformat()}]}}}), encoding="utf-8")
    monkeypatch.setattr(engine, "HISTORY_FILE", f)
    r = nexus_os._last_real_chat()
    assert r and r["status"] == nexus_os.ONLINE and r["reason"] == "RECENT_REAL_CHAT"


def test_last_real_chat_stale_is_none(tmp_path, monkeypatch):
    f = tmp_path / "h.json"
    f.write_text(json.dumps({"sessions": {"a": {"messages": [
        {"role": "assistant", "content": "x", "ts": "2020-01-01T00:00:00"}]}}}), encoding="utf-8")
    monkeypatch.setattr(engine, "HISTORY_FILE", f)
    assert nexus_os._last_real_chat() is None
