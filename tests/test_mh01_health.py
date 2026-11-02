import json
from nexus import health_watch as hw


def _fake(ok_core=True, ok_oll=True):
    def get(url, timeout=20):
        if "11434" in url:
            if not ok_oll: raise OSError("down")
            return {"models": []}, 1
        if not ok_core: raise OSError("down")
        if url.endswith("/api/health"): return {"ok": True, "score": 100}, 1
        if "hermes" in url: return {"cli_probe": "path_exists"}, 1
        if "downloads" in url: return {"success": True, "jobs": []}, 1
        return {"components": {"CORE": {"status": "ONLINE"}}, "orbital": {"CHAT": {"status": "ONLINE"}}}, 1
    return get


def _setup(tmp_path, monkeypatch):
    for n in ("LOG", "STATE"):
        monkeypatch.setattr(hw, n, tmp_path / (n + ".x"))


def test_healthy_writes_jsonl_no_restart(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    called = []
    r = hw.cycle(_fake(), restart=lambda: called.append(1) or 1, sb=lambda f, r: [])
    assert r["ok"] and not called
    line = json.loads((tmp_path / "LOG.x").read_text(encoding="utf-8").strip())
    assert line["checks"]["chat_probe"]["ok"] and line["tiles"]["CORE"] == "ONLINE"


def test_core_down_restarts_once_then_cooldown(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    called, sb = [], []
    r1 = hw.cycle(_fake(ok_core=False), restart=lambda: called.append(1) or 42, sb=lambda f, r: sb.append(f) or ["SB-AUTO-99"], now=1000.0)
    r2 = hw.cycle(_fake(ok_core=False), restart=lambda: called.append(1) or 43, sb=lambda f, r: [], now=1600.0)
    assert set(r1["failed"]) == {"core", "hermes", "downloader"} and r1["action"]["restart_pid"] == 42
    assert r2["action"]["restart"] == "SKIPPED_COOLDOWN_OR_LIMIT" and len(called) == 1 and sb


def test_ollama_down_detected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = hw.cycle(_fake(ok_oll=False), restart=lambda: 1, sb=lambda f, r: [], now=5000.0)
    assert r["failed"] == ["ollama"]
