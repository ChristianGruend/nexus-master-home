"""Stufe 2.3: Runner-Exec-API über Unix-Socket (Ausführung, cwd-Sperre, leere Umgebung, Limits)."""
import json
import os
import socket
import shutil
import stat
import tempfile
import threading
import time

import pytest

if os.name == "nt":
    pytest.skip("Unix-Socket-Runner nur POSIX", allow_module_level=True)

from nexus.runner.server import RunnerServer  # noqa: E402

pytestmark = pytest.mark.posix_only


@pytest.fixture
def runner(tmp_path):
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    sockdir = tempfile.mkdtemp(prefix="nxr")  # AF_UNIX-Pfade max. ~108 Zeichen
    sock = os.path.join(sockdir, "r.sock")
    srv = RunnerServer(sock, work)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield sock, work
    srv.shutdown()
    srv.server_close()
    shutil.rmtree(sockdir, ignore_errors=True)


def request(sock, method, path, body=None):
    raw = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else b"")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(30)
    s.connect(sock)
    s.sendall(f"{method} {path} HTTP/1.1\r\nHost: runner\r\nContent-Type: application/json\r\n"
              f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
    data = b""
    while chunk := s.recv(65536):
        data += chunk
    s.close()
    head, _, payload = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), payload


def events(payload):
    return [json.loads(line) for line in payload.decode().splitlines() if line.strip()]


def run(sock, **body):
    code, payload = request(sock, "POST", "/exec", body)
    assert code == 200, payload
    return events(payload)


def test_health(runner):
    code, payload = request(runner[0], "GET", "/health")
    assert code == 200 and json.loads(payload)["ok"] is True


def test_exec_echo(runner):
    ev = run(runner[0], command="echo hallo; echo fehler >&2; exit 3")
    assert ev[0]["t"] == "start"
    assert {"t": "out", "text": "hallo"} in ev
    assert {"t": "err", "text": "fehler"} in ev
    assert ev[-1]["t"] == "end" and ev[-1]["rc"] == 3 and ev[-1]["timeout"] is False


def test_argv(runner):
    ev = run(runner[0], argv=["printf", "%s", "a b"])
    assert {"t": "out", "text": "a b"} in ev


def test_cwd_inside(runner):
    ev = run(runner[0], command="pwd", cwd="sub")
    assert any(e.get("t") == "out" and e["text"].endswith("/sub") for e in ev)


@pytest.mark.parametrize("cwd", ["../", "/etc", "sub/../../", "fehlt"])
def test_cwd_escape(runner, cwd):
    code, payload = request(runner[0], "POST", "/exec", {"command": "pwd", "cwd": cwd})
    assert code == 400 and json.loads(payload)["error"] == "BAD_CWD"


def test_env_clean(runner, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", "geheim123")
    ev = run(runner[0], command="env")
    texts = [e["text"] for e in ev if e.get("t") == "out"]
    assert not any("geheim123" in t or "NEXUS_API_TOKEN" in t for t in texts)
    assert f"HOME={runner[1].resolve()}" in texts


def test_timeout(runner):
    t0 = time.time()
    ev = run(runner[0], command="sleep 30", timeout=1)
    assert ev[-1]["timeout"] is True
    assert time.time() - t0 < 10


def test_timeout_kills_background_children(runner):
    t0 = time.time()
    ev = run(runner[0], command="sleep 30 & sleep 30", timeout=1)
    assert ev[-1]["timeout"] is True
    assert time.time() - t0 < 10


def test_truncated(runner):
    ev = run(runner[0], command="yes | head -n 100000", max_output=1000)
    assert ev[-1]["truncated"] is True
    assert sum(len(e["text"]) for e in ev if e.get("t") == "out") < 2000


def test_client_disconnect_kills(runner):
    marker = runner[1] / "lebt"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(runner[0])
    body = json.dumps({"command": "for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do echo x; sleep 0.1; done; touch lebt"}).encode()
    s.sendall(b"POST /exec HTTP/1.1\r\nHost: runner\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
    s.recv(4096)
    s.close()
    time.sleep(3.5)
    assert not marker.exists()


def test_disconnect_kills_silent_process(runner):
    """Abbruch muss auch greifen, wenn der Prozess nichts ausgibt (kein fehlschlagendes Schreiben)."""
    marker = runner[1] / "ueberlebt"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(runner[0])
    body = json.dumps({"command": "sleep 2; touch ueberlebt"}).encode()
    s.sendall(b"POST /exec HTTP/1.1\r\nHost: runner\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
    s.recv(4096)
    s.close()
    time.sleep(3.5)
    assert not marker.exists()


def test_bad_requests(runner):
    assert request(runner[0], "POST", "/exec", b"nope")[0] == 400
    code, payload = request(runner[0], "POST", "/exec", {})
    assert code == 400 and json.loads(payload)["error"] == "NO_COMMAND"
    assert request(runner[0], "GET", "/nix")[0] == 404
    assert request(runner[0], "POST", "/andere", {"command": "id"})[0] == 404


def test_socket_mode(runner):
    assert stat.S_IMODE(os.stat(runner[0]).st_mode) == 0o660


def test_client_abort_kills_silent_process(runner, monkeypatch):
    """Ende-zu-Ende mit echtem Client: on_abort kappt die Verbindung, der Runner beendet den Prozess."""
    from nexus.runner import client
    monkeypatch.setenv("NEXUS_RUNNER_SOCKET", runner[0])
    aborts = []
    gen = client.exec_stream(command="sleep 2; touch ueberlebt2", on_abort=aborts.append, timeout=30)
    assert next(gen)["t"] == "start"
    threading.Timer(0.3, aborts[0]).start()
    rest = list(gen)  # endet, sobald die Verbindung gekappt ist
    assert all(e.get("t") != "end" or e.get("rc") != 0 for e in rest)
    time.sleep(3)
    assert not (runner[1] / "ueberlebt2").exists()
