"""Runner-Client (Stufe 2.3): Core fuehrt Agenten-Befehle im Runner-Container aus.

Der Runner hat kein Netz und keine Secrets; Core spricht ihn per HTTP/1.1 ueber einen Unix-Socket
in einem gemeinsamen Volume an. Nur stdlib. Ohne NEXUS_RUNNER_SOCKET bleibt alles beim Alten.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
from pathlib import Path

_CACHE: dict = {"t": 0.0, "v": False}
_LOCK = threading.Lock()


class RunnerError(RuntimeError):
    """Runner nicht erreichbar oder lehnt die Anfrage ab."""


def socket_path() -> str | None:
    p = os.environ.get("NEXUS_RUNNER_SOCKET", "").strip()
    return p or None


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float | None = None):
        super().__init__("runner", timeout=timeout if timeout is not None else 10)
        self._path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self._path)
        except Exception:
            s.close()
            raise
        self.sock = s


def _probe() -> bool:
    path = socket_path()
    if not path or not os.path.exists(path):
        return False
    conn = None
    try:
        conn = _UnixHTTPConnection(path, timeout=3)
        conn.request("GET", "/health")
        r = conn.getresponse()
        data = json.loads(r.read().decode("utf-8", "replace") or "{}")
        return r.status == 200 and bool(data.get("ok"))
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def available(ttl: float = 10.0) -> bool:
    """True, wenn ein Runner konfiguriert und gesund ist. Ergebnis wird ttl Sekunden gecacht. Wirft nie."""
    try:
        if not socket_path():
            return False
        now = time.monotonic()
        with _LOCK:
            if _CACHE["t"] and now - _CACHE["t"] < ttl and _CACHE.get("p") == socket_path():
                return bool(_CACHE["v"])
        v = _probe()
        with _LOCK:
            _CACHE.update(t=time.monotonic(), v=v, p=socket_path())
        return v
    except Exception:
        return False


def exec_stream(*, command: str | None = None, argv: list | None = None, cwd: str = "",
                timeout: int | float = 120, max_output: int | None = None, on_abort=None):
    """Fuehrt einen Befehl im Runner aus und liefert die NDJSON-Events als dicts.

    on_abort(fn) erhaelt eine Funktion, die die Verbindung aus einem anderen Thread kappt -> der Runner
    beendet die Prozessgruppe. (conn.sock taugt dafuer nicht: http.client setzt ihn bei Connection: close
    nach getresponse() auf None, die Antwort liest weiter ueber den rohen Socket.)
    Wird der Generator geschlossen, schliesst finally die Verbindung -> der Runner killt die Prozessgruppe.
    """
    path = socket_path()
    if not path:
        raise RunnerError("NEXUS_RUNNER_SOCKET nicht gesetzt")
    body: dict = {"cwd": cwd or "", "timeout": timeout}
    if argv:
        body["argv"] = [str(a) for a in argv]
    elif command is not None:
        body["command"] = command
    if max_output:
        body["max_output"] = int(max_output)
    conn = _UnixHTTPConnection(path, timeout=float(timeout) + 30)
    try:
        conn.connect()
        raw = conn.sock

        def _abort() -> None:
            try:
                raw.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        if on_abort is not None:
            on_abort(_abort)
        payload = json.dumps(body).encode("utf-8")
        conn.request("POST", "/exec", body=payload,
                     headers={"Content-Type": "application/json", "Content-Length": str(len(payload))})
        r = conn.getresponse()
        if r.status != 200:
            raw = r.read().decode("utf-8", "replace")
            try:
                msg = json.loads(raw).get("error") or raw
            except Exception:
                msg = raw or f"HTTP {r.status}"
            raise RunnerError(str(msg))
        while True:
            line = r.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
    finally:
        try:
            conn.close()
        except Exception:
            pass


def shared_dir() -> Path:
    """Core-seitiger Pfad des gemeinsamen Arbeitsvolumes (im Runner: /work)."""
    env = os.environ.get("NEXUS_RUNNER_SHARED", "").strip()
    if env:
        return Path(env)
    from nexus.common.paths import WORKSPACE
    return WORKSPACE / "runner"


def to_runner_cwd(path) -> str | None:
    """Relativer POSIX-Pfad zu /work, wenn path im gemeinsamen Volume liegt, sonst None."""
    try:
        p = Path(path).resolve()
        base = shared_dir().resolve()
        if p == base:
            return ""
        if p.is_relative_to(base):
            return p.relative_to(base).as_posix()
    except Exception:
        pass
    return None
