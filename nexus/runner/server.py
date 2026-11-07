"""NEXUS-Runner-Server (Stufe 2.3).

Läuft im Runner-Container ohne Netz und ohne Secrets. Core spricht ihn über einen
Unix-Socket in einem gemeinsamen Volume per HTTP/1.1 an; wer den Socket erreicht,
darf ausführen (Zugriff = Volume-Mount, Socket 0660).

Protokoll:
- GET  /health -> 200 {"ok": true, "work": "/work"}
- POST /exec   {"argv": [...]} | {"command": "..."} (-> sh -c), optional "cwd" (relativ
  zu /work), "timeout" (1..3600 s), "max_output" (Bytes)
  -> 200 application/x-ndjson, je Zeile ein Event:
     {"t":"start","pid":n} · {"t":"out"|"err","text":zeile} ·
     {"t":"end","rc":int|null,"sec":float,"timeout":bool,"truncated":bool[,"error":str]}
  Legt der Client auf, wird die Prozessgruppe sofort beendet.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import select
import signal
import socket
import socketserver
import subprocess
import threading
import time
from pathlib import Path

MAX_BODY = 1024 * 1024
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 3600
DEFAULT_MAX_OUTPUT = 2_000_000


def clean_env(work: Path) -> dict:
    """Feste, minimale Umgebung – nichts aus os.environ erben."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(work),
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def resolve_cwd(work: Path, rel) -> Path | None:
    root = Path(work).resolve()
    if rel is None or rel == "":
        return root
    if not isinstance(rel, str) or os.path.isabs(rel):
        return None
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_dir():
        return None
    return candidate


def _int_opt(value, default: int, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(lo, min(value, hi))


def _peer_closed(sock) -> bool:
    """True, wenn der Client aufgelegt hat (EOF/Fehler) – auch wenn der Prozess gerade nichts ausgibt."""
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return False
        return sock.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def address_string(self):  # client_address ist bei Unix-Sockets leer
        return "runner"

    def log_message(self, *args):
        pass

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"ok": True, "work": str(self.server.work)})
        else:
            self._send_json(404, {"error": "NOT_FOUND"})

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0:
            self._send_json(400, {"error": "BAD_CONTENT_LENGTH"})
            return None
        if length > MAX_BODY:
            self._send_json(413, {"error": "BODY_TOO_LARGE"})
            return None
        try:
            obj = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            obj = None
        if not isinstance(obj, dict):
            self._send_json(400, {"error": "INVALID_JSON"})
            return None
        return obj

    def do_POST(self):
        if self.path != "/exec":
            self._send_json(404, {"error": "NOT_FOUND"})
            return
        req = self._read_body()
        if req is None:
            return
        argv, command = req.get("argv"), req.get("command")
        if isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv):
            pass
        elif argv is None and isinstance(command, str) and command.strip():
            argv = ["sh", "-c", command]
        else:
            self._send_json(400, {"error": "NO_COMMAND"})
            return
        cwd = resolve_cwd(self.server.work, req.get("cwd"))
        if cwd is None:
            self._send_json(400, {"error": "BAD_CWD"})
            return
        timeout = _int_opt(req.get("timeout"), DEFAULT_TIMEOUT, 1, MAX_TIMEOUT)
        max_output = _int_opt(req.get("max_output"), DEFAULT_MAX_OUTPUT, 1, DEFAULT_MAX_OUTPUT)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self._stream(argv, cwd, timeout, max_output)

    def _stream(self, argv, cwd: Path, timeout: int, max_output: int) -> None:
        lock = threading.Lock()
        state = {"bytes": 0, "truncated": False, "gone": False, "proc": None}

        def emit(obj: dict) -> None:
            if state["gone"]:
                return
            try:
                self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                state["gone"] = True  # Client hat aufgelegt -> Prozess beenden
                if state["proc"] is not None:
                    _kill_group(state["proc"])

        t0 = time.monotonic()
        try:
            proc = subprocess.Popen(argv, cwd=str(cwd), env=clean_env(self.server.work),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True)
        except OSError as e:
            emit({"t": "end", "rc": None, "sec": 0, "timeout": False, "truncated": False, "error": str(e)})
            return
        state["proc"] = proc
        with lock:
            emit({"t": "start", "pid": proc.pid})

        def pump(stream, typ: str) -> None:
            for raw in iter(stream.readline, b""):
                with lock:
                    state["bytes"] += len(raw)
                    if state["bytes"] > max_output:
                        state["truncated"] = True
                    if not state["truncated"]:
                        emit({"t": typ, "text": raw.decode("utf-8", errors="replace").rstrip("\r\n")})
            stream.close()

        threads = [threading.Thread(target=pump, args=(proc.stdout, "out"), daemon=True),
                   threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True)]
        for t in threads:
            t.start()
        timed_out = False
        deadline = t0 + timeout
        while proc.poll() is None:
            if not state["gone"] and _peer_closed(self.connection):
                state["gone"] = True
            if state["gone"]:
                _kill_group(proc)
            elif time.monotonic() >= deadline:
                timed_out = True
                _kill_group(proc)
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
        _kill_group(proc)  # Reste der Gruppe (Hintergrundprozesse) mitnehmen
        for t in threads:
            t.join(timeout=5)
        with lock:
            emit({"t": "end", "rc": proc.returncode, "sec": round(time.monotonic() - t0, 2),
                  "timeout": timed_out, "truncated": state["truncated"]})


class RunnerServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, socket_path: str, work: Path, handler=Handler):
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        super().__init__(socket_path, handler)
        os.chmod(socket_path, 0o660)
        self.work = Path(work).resolve()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="NEXUS-Runner (Exec-API über Unix-Socket)")
    ap.add_argument("--socket", default=os.environ.get("NEXUS_RUNNER_SOCKET", "/run/nexus-runner/runner.sock"))
    ap.add_argument("--work", default="/work")
    args = ap.parse_args(argv)
    Path(args.socket).parent.mkdir(parents=True, exist_ok=True)
    os.umask(0o007)
    server = RunnerServer(args.socket, Path(args.work))

    def _term(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)
    print("runner bereit:", args.socket, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            os.unlink(args.socket)
        except OSError:
            pass


if __name__ == "__main__":
    main()
