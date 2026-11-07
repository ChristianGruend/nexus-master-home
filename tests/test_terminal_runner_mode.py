"""Stufe 2.3: Terminal im Runner-Modus – Guard bleibt in Core, ausgefuehrt wird nur ueber den Runner."""
import subprocess
import sys

import pytest

from nexus.runner import client as runner_client
from nexus.terminal import service

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Runner-Modus nur auf POSIX")


@pytest.fixture
def runner(monkeypatch):
    calls = []

    def fake_stream(*, command=None, argv=None, cwd="", timeout=120, max_output=None, on_abort=None):
        calls.append({"command": command, "argv": argv, "cwd": cwd})
        yield {"t": "start", "pid": 1}
        yield {"t": "out", "text": "x\n"}
        yield {"t": "end", "rc": 0, "sec": 0.01, "timeout": False, "truncated": False}

    def no_popen(*a, **k):
        raise AssertionError("subprocess.Popen darf im Runner-Modus nicht laufen")

    monkeypatch.setenv("NEXUS_RUNNER_SOCKET", "/nicht/da.sock")
    monkeypatch.setattr(runner_client, "exec_stream", fake_stream)
    monkeypatch.setattr(subprocess, "Popen", no_popen)
    return calls


def test_run_geht_ueber_runner(runner):
    r = service.run("echo x", wait=True)
    assert r["runner"] is True
    assert r["status"] == "OK" and r["returncode"] == 0
    assert r["stdout"].strip() == "x"
    assert runner[0]["command"] == "echo x" and runner[0]["argv"] is None


def test_python_shell_als_argv(runner):
    service.run("print(1)", shell="python", wait=True)
    assert runner[0]["argv"] == ["python3", "-B", "-c", "print(1)"]


def test_cwd_ausserhalb_shared_gibt_hinweis(runner):
    r = service.run("echo x", wait=True)
    assert runner[0]["cwd"] == ""
    assert any("Runner-Modus" in e.get("text", "") for e in service.history(50, run_id=r["run"]))


def test_guard_blockt_ohne_runner(runner):
    r = service.run("sudo reboot; shutdown now", wait=True)
    assert r["status"] == "BLOCKED"
    assert runner == []


def test_run_session_nimmt_runner(runner):
    r = service.run_session("echo x", wait=True)
    assert r["runner"] is True and r["stdout"].strip() == "x"


def test_runner_fehler_wird_error(monkeypatch):
    def boom(**k):
        raise runner_client.RunnerError("NO_COMMAND")
        yield  # pragma: no cover

    monkeypatch.setenv("NEXUS_RUNNER_SOCKET", "/nicht/da.sock")
    monkeypatch.setattr(runner_client, "exec_stream", boom)
    r = service.run("echo x", wait=True)
    assert r["status"] == "ERROR" and r["success"] is False and "NO_COMMAND" in r["error"]


def test_runner_down_fails_closed(monkeypatch, tmp_path):
    """Runner konfiguriert, aber nicht erreichbar -> ERROR, kein Rueckfall auf lokale Ausfuehrung."""
    import subprocess
    from nexus.terminal import service
    monkeypatch.setenv("NEXUS_RUNNER_SOCKET", str(tmp_path / "fehlt.sock"))

    def _no_popen(*a, **k):
        raise AssertionError("lokale Ausfuehrung trotz Runner-Konfiguration")

    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    res = service.run("echo x", wait=True)
    assert res["status"] == "ERROR" and res["success"] is False and res.get("runner") is True


def test_runner_multiline_keeps_newlines(monkeypatch):
    from nexus.runner import client as rc
    from nexus.terminal import service
    monkeypatch.setenv("NEXUS_RUNNER_SOCKET", "/nicht/da.sock")

    def fake(**kw):
        yield {"t": "start", "pid": 1}
        yield {"t": "out", "text": "a"}
        yield {"t": "out", "text": "b"}
        yield {"t": "end", "rc": 0, "sec": 0.1, "timeout": False, "truncated": False}

    monkeypatch.setattr(rc, "exec_stream", fake)
    r = service.run("printf 'a\\nb\\n'", wait=True)
    assert r["stdout"] == "a\nb\n"
