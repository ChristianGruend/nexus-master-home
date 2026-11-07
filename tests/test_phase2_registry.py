"""Phase 2 Schritt 7/8: pc_music und shell_run ueber die Registry, Schalter, Bridge-Sperre."""
import contextlib
from nexus.safety import dispatch, gate
from nexus.bridge import executors
from nexus.bridge.permissions import TASK_PERMISSIONS


def test_registered():
    reg = dispatch.registry()
    assert reg["pc_music"] is executors.exec_pc_music
    assert reg["shell_run"] is executors.exec_shell_run
    assert "pc_music" in TASK_PERMISSIONS and "shell_run" in TASK_PERMISSIONS


def test_pc_music_via_registry(monkeypatch):
    calls = []
    import nexus.computer.remote_executor as rx
    import nexus.maintenance as mt
    monkeypatch.setattr(rx, "execute", lambda i, a: calls.append((i, a)) or {"success": True, "evidence": {"verified": True}})
    class _M:
        def work(self, name):
            return contextlib.nullcontext()
    monkeypatch.setattr(mt, "maintenance", lambda: _M())
    r = dispatch.run_task("pc_music", {"intent": "media.pause", "args": {"target": "youtube"}})
    assert r["success"] and calls == [("media.pause", {"target": "youtube"})]
    assert gate.check("NEXUS", "pc_music").allowed


def test_bridge_calls_rejected():
    assert executors.exec_pc_music({}, object())["error"] == "IN_PROCESS_ONLY"
    assert executors.exec_shell_run({"command": "dir"}, object())["error"] == "IN_PROCESS_ONLY"


def test_shell_run_ignores_old_switch(monkeypatch):
    from nexus.terminal import service
    seen = []
    monkeypatch.setattr(service, "execute", lambda c, **kw: seen.append((c, kw)) or {"ok": True})
    monkeypatch.setenv("NEXUS_PHASE2_REGISTRY", "1")
    assert dispatch.shell_execute("dir", agent="NEXUS", wait=True) == {"ok": True}
    monkeypatch.setenv("NEXUS_PHASE2_REGISTRY", "0")
    assert dispatch.shell_execute("dir", agent="NEXUS") == {"ok": True}
    assert seen == [("dir", {"agent": "NEXUS", "wait": True}), ("dir", {"agent": "NEXUS"})]


def test_shell_run_still_blocks_c_drive_write():
    r = dispatch.shell_execute("Set-Content C:\\phase2_test.txt x", agent="NEXUS", wait=True)
    assert r.get("ok") is not True
