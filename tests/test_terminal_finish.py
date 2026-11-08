"""NEXUS-Terminal Abschluss (20261031): NEXUS-Chat/ShellExecutor, Hermes, NEXUS GPT ueber das Terminal; UI-Geheimnis."""
import json
import time
from pathlib import Path

import pytest

from nexus.terminal import guard, service


def _ev():
    fp = Path(service.LOG_FILE)
    return [json.loads(x) for x in fp.read_text(encoding="utf-8").splitlines()] if fp.exists() else []


def test_secret_generated_local_not_in_code_or_log():
    s1 = guard.ui_secret()
    assert len(s1) >= 32 and guard.ui_secret() == s1
    assert (Path(service.TERM_DIR) / "ui_secret.txt").read_text(encoding="utf-8").strip() == s1
    assert guard.check_ui_cookie("a=1; nx_term_ui=" + s1)
    assert not guard.check_ui_cookie("nx_term_ui=falsch") and not guard.check_ui_cookie(None) and not guard.check_ui_cookie("")
    assert "HttpOnly" in guard.ui_cookie() and "SameSite=Strict" in guard.ui_cookie()
    root = service.ROOT
    for f in ("nexus/terminal/guard.py", "nexus/terminal/service.py", "nexus/chat/server.py", "nexus/chat/routes/ui.py",
              "nexus/chat/routes/terminal.py", "nexus/chat/static/index.html"):
        assert s1 not in (root / f).read_text(encoding="utf-8")
    guard.audit("x", note="test")
    assert s1 not in guard.audit_file().read_text(encoding="utf-8")


def test_terminal_config_files_not_reachable_by_agents():
    for c in ("Get-Content workspace/terminal/ui_secret.txt", "Set-Content workspace/terminal/permissions.json x",
              "type approvals.json", "Remove-Item workspace/terminal/audit.jsonl"):
        r = service.execute(c, agent="NEXUS", shell="session")
        assert r["status"] == "BLOCKED" and "TERMINAL_KONFIG" in r["error"], c


@pytest.mark.skipif(not service.pwsh_path(), reason="pwsh fehlt")
def test_nexus_chat_shell_goes_through_terminal_with_eddie_approval():
    from nexus.computer.shell import ShellExecutor
    r = ShellExecutor().run("Write-Output NEXUS_CHAT_OK", shell="powershell")
    assert r["error"] == "REGISTERED_ACTION_REQUIRED" and r["returncode"] is None and r["approval_id"]
    assert not r["success"] and r["via"] == "nexus-terminal"
    ev = _ev()
    assert any(e["type"] == "blocked" and e["agent"] == "NEXUS" and "APPROVAL_REQUIRED" in e.get("reason", "") for e in ev)
    d = guard.decide(r["approval_id"], True, by="test")
    assert d["started_run"]
    for _ in range(100):
        v = [e for e in _ev() if e.get("run") == d["started_run"] and e["type"] == "verdict"]
        if v:
            break
        time.sleep(0.1)
    assert v and v[0]["ok"], v
    assert any(e.get("run") == d["started_run"] and e.get("text") == "NEXUS_CHAT_OK" for e in _ev())
    acts = [a["action"] for a in guard.audit_tail(50)]
    assert "approval_autorun" in acts and "run_result" in acts
    service.reset_session("NEXUS")


def test_nexus_chat_blocked_paths_and_level_mode(monkeypatch):
    from nexus.computer.shell import ShellExecutor
    r = ShellExecutor().run("Get-ChildItem C:/Windows", shell="powershell")
    assert r["status"] == "BLOCKED" and not r["success"] and "C_DRIVE" in r["error"]
    assert ShellExecutor().run_script("gibt_es_nicht_123.ps1")["error"] == "script_not_found"
    monkeypatch.setattr(guard, "chat_shell_mode", lambda: "level")
    ok = ShellExecutor().run("print('lvl-ok')", shell="python")
    assert ok["success"] and ok["returncode"] == 0 and "lvl-ok" in ok["stdout"] and ok["verdict"] == "ERFOLG"


def test_hermes_script_runs_live_through_terminal():
    from nexus.bridge import executors
    from nexus.integrations.hermes_client import PROBE_SCRIPT
    if not (service.ROOT / PROBE_SCRIPT).exists():
        pytest.skip("Probe-Skript fehlt")
    out = executors.exec_python_run_project_script({"script": PROBE_SCRIPT}, None)
    assert out["returncode"] == 0 and out["terminal_run"] and out["child_pid"]
    ev = [e for e in _ev() if e.get("run") == out["terminal_run"]]
    assert ev[0]["type"] == "start" and ev[0]["agent"] == "Hermes" and ev[0]["shell"] == "argv"
    assert any(e["type"] == "verdict" for e in ev)


def test_hermes_level_denied(monkeypatch):
    from nexus.bridge import executors
    from nexus.integrations.hermes_client import PROBE_SCRIPT
    if not (service.ROOT / PROBE_SCRIPT).exists():
        pytest.skip("Probe-Skript fehlt")
    monkeypatch.setattr(guard, "agent_level", lambda a: "READ_ONLY")
    with pytest.raises(executors.BridgeError):
        executors.exec_python_run_project_script({"script": PROBE_SCRIPT}, None)


def test_gpt_run_tool_and_tool_ask(monkeypatch):
    from nexus.integrations import gpt_local_agent as g
    r = g.run_tool("print('gpt-tool-ok')", shell="python")
    assert r["success"] and r["verdict"] == "ERFOLG" and "gpt-tool-ok" in r["stdout"]
    assert any(e["agent"] == "NEXUS-GPT" and e["type"] == "start" for e in _ev())
    calls = []

    def fake_ask(msg, *a, **k):
        calls.append(k.get("system"))
        if k.get("fmt") == "json":
            return {"success": True, "reply": json.dumps({"command": "'GPT-' + (1+1)", "why": "test"})}
        return {"success": True, "reply": "Ergebnis: GPT-2"}
    monkeypatch.setattr(g, "ask", fake_ask)
    t = g.tool_ask("Rechne 1+1 im Terminal")
    assert t["command"] and t["terminal"]["run"] and t["reply"] and calls[0] == g.TOOL_PROMPT
    bad = (lambda *a, **k: {"success": True, "reply": "kein json"})
    monkeypatch.setattr(g, "ask", bad)
    assert g.tool_ask("x")["error"] == "GPT_TOOLCALL_INVALID"
    assert g.tool_ask("")["error"] == "TASK_REQUIRED"


def test_server_wiring():
    routes = service.ROOT / "nexus" / "chat" / "routes"
    src = "".join(p.read_text(encoding="utf-8") for p in sorted(routes.glob("*.py")))
    assert "api_auth.human_approval" in src and "/api/gpt/tool" in src and "Set-Cookie" in src
    html = (service.ROOT / "nexus" / "chat" / "static" / "index.html").read_text(encoding="utf-8")
    assert "__nxStableLoad(function(){" in html and html.count("__nxStableLoad(") >= 2 and "nxStableJs" in html
