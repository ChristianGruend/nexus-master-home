"""Tests: Erfolgspruefung, Abbruch, Rechtestufen, Freigaben, Audit (NEXUS-Terminal 20261031)."""
import json
import threading
import time

import pytest

from nexus.terminal import guard, service


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "TERM_DIR", tmp_path)
    monkeypatch.setattr(service, "LOG_FILE", tmp_path / "terminal.jsonl")
    yield tmp_path
    for a in list(service._SESSIONS):
        service._SESSIONS[a].stop()


def test_levels_classify():
    assert guard.required_level("Get-ChildItem")[0] == "READ_ONLY"
    assert guard.required_level("git status; git log -1")[0] == "READ_ONLY"
    assert guard.required_level("Set-Content G:/x.txt a")[0] == "SAFE_WRITE"
    assert guard.required_level("python -c 1")[0] == "SAFE_WRITE"
    for c in ("pip install x", "Start-Process notepad", "git push origin main", "winget install x",
              "Invoke-WebRequest http://x", "Stop-Process -Id 1", "Get-ChildItem D:/"):
        assert guard.required_level(c)[0] == "PRIVILEGED", c


def test_level_from_config_not_caller(_tmp):
    (_tmp / "permissions.json").write_text(json.dumps({"agents": {"NEXUS-GPT": "READ_ONLY"}}), encoding="utf-8")
    r = service.execute("Set-Content -Path x.txt -Value 1", agent="NEXUS-GPT", shell="python")
    assert r["status"] == "LEVEL_DENIED" and r["verdict"] == "FEHLER"
    r2 = service.execute("print('hi')", agent="NEXUS-GPT", shell="python")
    assert r2["status"] == "LEVEL_DENIED"  # Python braucht SAFE_WRITE


def test_privileged_needs_single_use_approval(_tmp):
    cmd = "Stop-Process -Id 999999 -ErrorAction SilentlyContinue; 'x'"
    r = service.execute(cmd, agent="NEXUS", shell="pwsh", timeout=60)
    assert r["status"] == "APPROVAL_REQUIRED" and r["approval_id"].startswith("apr_") and not r["ok"]
    assert service.execute(cmd, agent="NEXUS", shell="pwsh", approval_id="apr_fake")["status"] == "APPROVAL_REQUIRED"
    assert any(a["approval_id"] == r["approval_id"] for a in guard.pending_approvals())
    d = guard.decide(r["approval_id"], True)
    assert d["state"] == "approved" and d.get("started_run")  # Freigabe startet den Lauf selbst
    import time as _t
    for _ in range(100):
        if not any(a.get("run") == d["started_run"] for a in service.active_runs()):
            break
        _t.sleep(0.1)
    r4 = service.execute(cmd, agent="NEXUS", shell="pwsh", approval_id=r["approval_id"])
    assert r4["status"] == "APPROVAL_REQUIRED"  # einmalig
    # an Befehl gebunden
    r5 = service.execute("pip install foo", agent="NEXUS", approval_id=r["approval_id"])
    assert r5["status"] == "APPROVAL_REQUIRED"


def test_denied_approval(_tmp):
    r = service.execute("pip install foo", agent="Hermes")
    assert guard.decide(r["approval_id"], False)["state"] == "denied"
    assert service.execute("pip install foo", agent="Hermes", approval_id=r["approval_id"])["status"] == "APPROVAL_REQUIRED"


def test_c_and_destructive_still_blocked(_tmp):
    for c in ("Get-ChildItem C:/Windows", "Format-Volume -DriveLetter G", "git reset --hard"):
        r = service.execute(c, agent="Eddie")
        assert r["status"] == "BLOCKED" and r["verdict"] == "FEHLER"


def test_verdict_rules():
    assert guard.verdict({"status": "OK", "returncode": 0, "stdout": "x"})["ok"]
    assert "LEERE_AUSGABE" in guard.verdict({"status": "OK", "returncode": 0, "stdout": ""})["reasons"]
    assert guard.verdict({"status": "OK", "returncode": 0, "stdout": ""}, allow_empty=True)["ok"]
    assert "EXIT_2" in guard.verdict({"status": "FAILED", "returncode": 2, "stdout": "x"})["reasons"]
    assert "KEIN_EXIT_CODE" in guard.verdict({"status": "ERROR", "stdout": "x"})["reasons"]
    assert "FEHLERTEXT_IN_AUSGABE" in guard.verdict({"status": "OK", "returncode": 0, "stdout": "Traceback (most recent call last):\n"})["reasons"]


def test_structured_result_python(_tmp):
    r = service.execute("print('ok-1')", agent="Hermes", shell="python")
    assert r["ok"] and r["verdict"] == "ERFOLG" and r["returncode"] == 0 and "ok-1" in r["stdout"]
    e = service.execute("pass", agent="Hermes", shell="python")
    assert not e["ok"] and "LEERE_AUSGABE" in e["reasons"]
    f = service.execute("raise SystemExit(3)", agent="Hermes", shell="python")
    assert f["verdict"] == "FEHLER" and "EXIT_3" in f["reasons"]
    ev = [json.loads(x) for x in service.LOG_FILE.read_text(encoding="utf-8").splitlines()]
    assert any(x["type"] == "verdict" and x["ok"] for x in ev)


@pytest.mark.skipif(not service.pwsh_path(), reason="pwsh fehlt")
def test_session_verdict_and_cancel(_tmp):
    ok = service.execute("'hallo'", agent="NEXUS", timeout=60)
    assert ok["verdict"] == "ERFOLG"
    bad = service.execute("Write-Error boom", agent="NEXUS", timeout=60)
    assert bad["verdict"] == "FEHLER"
    st = service.execute("Start-Sleep 30; 'nie'", agent="NEXUS", timeout=60, wait=False)
    rid = st["run"]
    for _ in range(50):
        if any(a["run"] == rid for a in service.active_runs()):
            break
        time.sleep(0.1)
    time.sleep(1.0)
    assert service.cancel(rid)["cancelled"]
    for _ in range(100):
        if not any(a["run"] == rid for a in service.active_runs()):
            break
        time.sleep(0.1)
    ev = [json.loads(x) for x in service.LOG_FILE.read_text(encoding="utf-8").splitlines() if rid in x]
    v = [x for x in ev if x["type"] == "verdict"]
    assert v and not v[0]["ok"] and "CANCELLED" in v[0]["reasons"]
    again = service.execute("'wieder da'", agent="NEXUS", timeout=60)
    assert again["ok"]


def test_cancel_subprocess(_tmp):
    st = service.execute("import time\nprint('a',flush=True)\ntime.sleep(30)", agent="Hermes", shell="python", wait=False, timeout=60)
    time.sleep(1.5)
    assert service.cancel(st["run"])["success"]
    for _ in range(100):
        if not service.active_runs():
            break
        time.sleep(0.1)
    ev = [json.loads(x) for x in service.LOG_FILE.read_text(encoding="utf-8").splitlines() if st["run"] in x]
    assert any(x["type"] == "verdict" and "CANCELLED" in x["reasons"] for x in ev)
    assert not service.cancel("nope")["success"]


def test_audit_all_actions(_tmp):
    service.execute("print(1)", agent="Hermes", shell="python")
    service.execute("Get-ChildItem C:/", agent="Hermes")
    r = service.execute("pip install foo", agent="Hermes")
    guard.decide(r["approval_id"], False)
    acts = {a["action"] for a in guard.audit_tail(100)}
    assert {"run_request", "policy_decision", "run_result", "run_blocked", "approval_requested", "approval_denied"} <= acts


def test_server_routes_present(monkeypatch):
    from _http import nexus_client
    monkeypatch.setattr(guard, "audit", lambda *a, **k: None)
    with nexus_client() as c:
        for p in ("/api/terminal/state", "/api/terminal/audit"):
            assert c.get(p).json()["success"] is True, p
        assert c.post("/api/terminal/cancel", json={"run": "nope"}).status_code == 200
        r = c.post("/api/terminal/approve", json={"approval_id": "x"})  # ohne UI-Cookie
        assert r.status_code == 403 and r.json()["error"] == "NUR_EDDIE_UI"
    html = open(service.ROOT / "nexus" / "chat" / "static" / "index.html", encoding="utf-8").read()
    assert 'id="btnTerminal"' in html and "data-cancel" in html and "Freigeben" in html and 'e.type==="verdict"' in html
