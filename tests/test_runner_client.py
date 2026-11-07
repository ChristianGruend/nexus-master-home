"""Stufe 2.3: Runner-Client gegen einen Fake-Runner (Unix-Socket, eigener Minimal-Handler)."""
import json
import os
import socketserver
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="AF_UNIX/posix")

from nexus.runner import client  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def address_string(self):
        return "unix"

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "work": "/work"})
        else:
            self._json(404, {"error": "NOT_FOUND"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if body.get("cwd") == "../evil":
            return self._json(400, {"error": "BAD_CWD"})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")
        self.end_headers()
        for ev in ({"t": "start", "pid": 7}, {"t": "out", "text": "hallo " + str(body.get("command"))},
                   {"t": "err", "text": "warn"},
                   {"t": "end", "rc": 0, "sec": 0.1, "timeout": False, "truncated": False}):
            self.wfile.write((json.dumps(ev) + "\n").encode())
            self.wfile.flush()
        self.close_connection = True


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


@pytest.fixture
def fake(monkeypatch):
    d = tempfile.mkdtemp(prefix="nxr")  # kurzer Pfad (AF_UNIX-Limit)
    sock = os.path.join(d, "r.sock")
    srv = _Server(sock, _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    monkeypatch.setenv("NEXUS_RUNNER_SOCKET", sock)
    client._CACHE.update(t=0.0, v=False, p=None)
    yield sock
    srv.shutdown()
    srv.server_close()
    client._CACHE.update(t=0.0, v=False, p=None)


def test_available_ohne_env(monkeypatch):
    monkeypatch.delenv("NEXUS_RUNNER_SOCKET", raising=False)
    client._CACHE.update(t=0.0, v=False, p=None)
    assert client.socket_path() is None
    assert client.available() is False


def test_available_socket_fehlt(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_RUNNER_SOCKET", str(tmp_path / "nix.sock"))
    client._CACHE.update(t=0.0, v=False, p=None)
    assert client.available() is False


def test_available_mit_fake(fake):
    assert client.available(ttl=0) is True


def test_exec_stream_events(fake):
    evs = list(client.exec_stream(command="x", timeout=5))
    assert [e["t"] for e in evs] == ["start", "out", "err", "end"]
    assert evs[1]["text"] == "hallo x"
    assert evs[-1]["rc"] == 0


def test_exec_stream_400(fake):
    with pytest.raises(client.RunnerError, match="BAD_CWD"):
        list(client.exec_stream(command="x", cwd="../evil", timeout=5))


def test_exec_stream_ohne_socket(monkeypatch):
    monkeypatch.delenv("NEXUS_RUNNER_SOCKET", raising=False)
    with pytest.raises(client.RunnerError):
        list(client.exec_stream(command="x"))


def test_to_runner_cwd(monkeypatch, tmp_path):
    shared = tmp_path / "runner"
    (shared / "sub" / "dir").mkdir(parents=True)
    monkeypatch.setenv("NEXUS_RUNNER_SHARED", str(shared))
    assert client.shared_dir() == shared
    assert client.to_runner_cwd(shared) == ""
    assert client.to_runner_cwd(shared / "sub" / "dir") == "sub/dir"
    assert client.to_runner_cwd(tmp_path) is None
    assert client.to_runner_cwd(tmp_path / "runner-evil") is None
    assert client.to_runner_cwd(shared / ".." / "x") is None
