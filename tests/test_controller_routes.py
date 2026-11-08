"""Vertragstests für die aus dem Controller ausgelagerten Direktpfade."""
from types import SimpleNamespace

from nexus.master import controller as controller_module
from nexus.master.controller import MasterController


def _controller():
    # Keine produktiven Dienste/Dateien in einem Routing-Test initialisieren.
    return MasterController.__new__(MasterController)


def test_multistep_request_skips_direct_execution(monkeypatch):
    monkeypatch.setattr(controller_module, "run_tools", lambda *_: 1 / 0)
    result = _controller()._early_or_special(
        "1. Lies workspace/files/a.txt\n2. Schreibe workspace/files/b.txt",
        [], {"early": True, "reply": "nicht ausführen"}, "file", [],
    )
    assert result is None


def test_write_and_read_request_goes_to_agent_loop(monkeypatch):
    # Die zwei expliziten Toolaktionen gelten seit jeher als Mehrschrittauftrag.
    monkeypatch.setattr(controller_module, "run_tools", lambda *_: 1 / 0)
    result = _controller()._early_or_special(
        "write_file workspace/files/a.txt inhalt exakt: OK; danach read_file",
        [], {}, "build", [],
    )
    assert result is None


def test_direct_read_uses_injected_executor_and_preserves_result(monkeypatch):
    ctrl = _controller()
    calls = []

    def fake_tools(batch, owner):
        assert owner is ctrl
        calls.extend(batch)
        return [{"name": "read_file", "args": batch[0]["args"],
                 "result": {"success": True, "content": "OK", "bytes": 2}}]

    monkeypatch.setattr(controller_module, "run_tools", fake_tools)
    result = ctrl._early_or_special("Lies workspace/files/a.txt", [], {}, "file", [])
    assert calls == [{"name": "read_file", "args": {"path": "workspace/files/a.txt"}}]
    assert result is not None
    assert result["success"] is True
    assert result["data"]["execution_status"] == "TOOLS_SUCCEEDED"
    assert "OK" in result["reply"]


def test_direct_powershell_preserves_exit_code_and_checkpoint(monkeypatch):
    ctrl = _controller()
    states = []
    calls = []
    monkeypatch.setattr(ctrl, "shell", SimpleNamespace(run=lambda command, **kw: (
        calls.append((command, kw)) or
        {"success": True, "returncode": 0, "stdout": "fertig", "stderr": ""}
    )), raising=False)
    monkeypatch.setattr(ctrl, "_write_task_state", lambda **kw: states.append(kw))
    result = ctrl._early_or_special("Führe PowerShell-Befehl wirklich aus: Write-Output fertig", [], {}, "shell", [])
    assert calls == [("Write-Output fertig", {"shell": "powershell"})]
    assert result is not None
    assert result["success"] is True and result["data"]["execution_status"] == "TOOLS_SUCCEEDED"
    assert "returncode: 0" in result["reply"] and states[0]["status"] == "PASS"


def test_router_diagnosis_is_read_only(monkeypatch):
    ctrl = _controller()
    calls = []

    def fake_tools(batch, owner):
        assert owner is ctrl
        calls.extend(batch)
        return [{"name": "read_file", "args": batch[0]["args"],
                 "result": {"success": True, "content": "", "bytes": 0}}]

    monkeypatch.setattr(controller_module, "run_tools", fake_tools)
    result = ctrl._early_or_special("Diagnose tool-router", [], {}, "diagnose", [])
    assert result is not None
    assert result["success"] is True
    assert result["data"]["execution_status"] == "TOOLS_SUCCEEDED"
    assert len(calls) == 4 and {c["name"] for c in calls} == {"read_file"}
    assert "Schreibaktionen: 0" in result["reply"]


def test_early_reply_and_unhandled_intent():
    ctrl = _controller()
    result = ctrl._early_or_special("Hallo", [], {"early": True, "reply": "OK"}, "other", [])
    assert result is not None
    assert result["success"] is True and result["reply"] == "OK"
    assert ctrl._early_or_special("Hallo", [], {}, "other", []) is None


def test_verifier_requires_evidence_after_write():
    ctrl = _controller()
    write = {"name": "write_file", "result": {"success": True}}
    read = {"name": "read_file", "result": {"success": True}}
    assert ctrl._verify_goal_completion("Schreibe Datei und verifiziere", "build", [write]) == (
        False, "goal_write_verify_not_proven")
    assert ctrl._verify_goal_completion("Schreibe Datei und verifiziere", "build", [write, read]) == (True, None)
    assert ctrl._verify_goal_completion("Schreibe Datei und verifiziere", "build", [write, read,
        {"name": "shell", "result": {"success": False}}]) == (False, "tool_failed_or_unconfirmed")
