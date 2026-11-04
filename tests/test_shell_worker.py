import json
import subprocess
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from nexus.common.paths import ROOT
from nexus.integrations import aether_supervisor as aether
from nexus.terminal import shell_worker


def powershell() -> str:
    return shell_worker.powershell_exe()


@pytest.fixture()
def worker_env(tmp_path, monkeypatch):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    audit = tmp_path / "logs" / "shell-audit.jsonl"
    monkeypatch.setenv("WORKER_PS1", str(ROOT / "tools" / "worker.ps1"))
    monkeypatch.setenv("WORKER_SANDBOX", str(sandbox))
    monkeypatch.setenv("WORKER_AUDIT", str(audit))
    monkeypatch.setattr(aether, "SHELL_GUARD_STATE", tmp_path / "shell_guard.json")
    aether.reset_shell_guard()
    return sandbox, audit


def test_ok_cat_and_audit(worker_env):
    sandbox, audit = worker_env
    (sandbox / "hello.txt").write_text("HALLO NEXUS\n", encoding="utf-8")
    r = shell_worker.run("cat", ["hello.txt"])
    assert r["ok"] and r["exit_code"] == 0 and "HALLO NEXUS" in r["stdout"]
    assert r["denied"] is False
    rec = json.loads(audit.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["cmd"] == "cat" and rec["args"] == ["hello.txt"]
    assert rec["exit_code"] == 0 and rec["user_confirmed"] is False
    assert "duration_ms" in rec and "stdout_preview" in rec


def test_denied_returns_worker_text(worker_env):
    r = shell_worker.run("rm", ["-rf", "."])
    assert r["ok"] is False and r["denied"] is True and r["exit_code"] == 126
    assert r["error"].startswith("DENIED:")


def test_timeout_is_enforced(worker_env, tmp_path, monkeypatch):
    slow = tmp_path / "slow.ps1"
    slow.write_text("Start-Sleep -Seconds 8\nWrite-Output '{}'\n", encoding="utf-8")
    monkeypatch.setenv("WORKER_PS1", str(slow))
    monkeypatch.setattr(shell_worker, "HARD_MARGIN", 0)
    r = shell_worker.run("pwd", [], timeout=1)
    assert r["ok"] is False and r["timed_out"] is True and r["exit_code"] == 124
    assert r["error"].startswith("TIMEOUT:")


def test_broken_json(worker_env, tmp_path, monkeypatch):
    broken = tmp_path / "broken.ps1"
    broken.write_text("Write-Output 'not json'\n", encoding="utf-8")
    monkeypatch.setenv("WORKER_PS1", str(broken))
    r = shell_worker.run("pwd", [])
    assert r["ok"] is False and r["error"].startswith("INVALID_WORKER_JSON:")


def test_missing_worker(worker_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKER_PS1", str(tmp_path / "missing-worker.ps1"))
    r = shell_worker.run("pwd", [])
    assert r["ok"] is False and r["error"].startswith("WORKER_PS1_MISSING:")


SECURITY_CASES = [
    ("rm", ["-rf", "."]),
    ("python3", ["-c", "print(1)"]),
    ("find", [".", "-delete"]),
    ("find", [".", "-exec", "cmd"]),
    ("grep", ["-rf", "x"]),
    ("tail", ["-f", "hello.txt"]),
    ("cat", ["..\\x"]),
    ("cat", ["C:\\Windows\\win.ini"]),
    ("cat", ["\\\\server\\share"]),
    ("cat", ["nul"]),
    ("cat", ["a.txt:stream"]),
    ("ls", ["~"]),
    ("echo", ["x;y"]),
    ("echo", ["x|y"]),
    ("echo", ["x&y"]),
    ("echo", ["x$y"]),
]


def test_security_regressions_all_denied(worker_env, monkeypatch):
    monkeypatch.setattr(aether, "SHELL_DENY_LIMIT", 9999)
    sandbox, _ = worker_env
    (sandbox / "hello.txt").write_text("x\n", encoding="utf-8")
    results = [shell_worker.run(cmd, args) for cmd, args in SECURITY_CASES]
    bad = [(c, r.get("error")) for (c, _), r in zip(SECURITY_CASES, results) if not r.get("denied")]
    assert not bad, bad
    for r in results:
        assert str(r["error"]).startswith("DENIED:")


def test_selftest_ok():
    ps1 = ROOT / "tools" / "worker.ps1"
    proc = subprocess.run([powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                           "-File", str(ps1), "--selftest"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "OK"


def test_hermes_tool_returns_stdout_or_error(worker_env):
    from nexus.integrations import hermes_tools
    sandbox, _ = worker_env
    (sandbox / "note.txt").write_text("NEXUS-TOOL\n", encoding="utf-8")
    assert "NEXUS-TOOL" in hermes_tools.run_tool("shell_readonly", {"cmd": "cat", "args": ["note.txt"]}, sandbox)
    denied = hermes_tools.run_tool("shell_readonly", {"cmd": "cat", "args": ["C:\\Windows\\win.ini"]}, sandbox)
    assert denied.startswith("DENIED:")


def test_hermes_native_loop_uses_shell_readonly(worker_env):
    from nexus.integrations import hermes_tools as ht
    sandbox, _ = worker_env
    (sandbox / "note.txt").write_text("NEXUS-LOOP\n", encoding="utf-8")
    assert ht.wants_tools("lies die Datei note.txt")
    seq = [{"content": "", "tool_calls": [{"function": {"name": "shell_readonly",
                                                        "arguments": {"cmd": "cat", "args": ["note.txt"]}}}]},
           {"content": "Gelesen: NEXUS-LOOP"}]
    with mock.patch.object(ht, "_chat", side_effect=seq), mock.patch.object(ht, "_log"):
        r = ht.native_loop("lies note.txt", sandbox, "m", "HX-SW")
    assert r["reply"] == "Gelesen: NEXUS-LOOP"
    assert r["tool_calls"][0]["tool"] == "shell_readonly"
    assert r["tool_calls"][0]["ok"] is True
    assert "NEXUS-LOOP" in r["tool_calls"][0]["result_preview"]


def test_aether_locks_after_five_denials(worker_env, tmp_path, monkeypatch):
    sandbox, audit = worker_env
    audit.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    with audit.open("w", encoding="utf-8") as fh:
        for i in range(5):
            fh.write(json.dumps({"timestamp": now.isoformat(timespec="milliseconds"),
                                 "cmd": "cat", "args": ["C:\\x"], "cwd": ".",
                                 "exit_code": 126, "duration_ms": 1, "user_confirmed": False,
                                 "stdout_preview": "", "error": f"DENIED: TEST_{i}"}) + "\n")
    live = tmp_path / "live.jsonl"
    from nexus.common import live_events
    monkeypatch.setattr(live_events, "LIVE_FILE", live)
    monkeypatch.setenv("NEXUS_LIVE_FORCE", "1")
    guard = aether.check_shell_guard(agent="Aether", emit_live=True)
    assert guard["blocked"] is True and "5 DENIED" in guard["reason"]
    assert "Aether" in live.read_text(encoding="utf-8")
    (sandbox / "hello.txt").write_text("x\n", encoding="utf-8")
    r = shell_worker.run("cat", ["hello.txt"])
    assert r["ok"] is False and r["error"].startswith("DENIED: TOOL_TEMPORARILY_LOCKED")
