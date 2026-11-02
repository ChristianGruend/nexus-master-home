#!/usr/bin/env python3
"""NEXUS Bridge Allowlist Gateway.
Listens on 127.0.0.1:GATEWAY_PORT (default 8300), proxies only allowlisted
bridge paths to ORIGIN (default http://127.0.0.1:8299). Rate-limit 60/min,
body max 1MB, timeouts. Logs without secrets.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Deque, Dict, Tuple
from pathlib import Path
from urllib.parse import urlsplit

ORIGIN = os.environ.get("NEXUS_GATEWAY_ORIGIN", "http://127.0.0.1:8299").rstrip("/")
LISTEN_HOST = os.environ.get("NEXUS_GATEWAY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("NEXUS_GATEWAY_PORT", "8300"))
try:
    from nexus.common.paths import BRIDGE_LOGS as _BRIDGE_LOGS
    _default_log = str(_BRIDGE_LOGS)
except Exception:
    _default_log = str(Path(__file__).resolve().parents[2] / "logs" / "bridge")
LOG_DIR = os.environ.get("NEXUS_GATEWAY_LOG_DIR", _default_log)
MAX_BODY = int(os.environ.get("NEXUS_GATEWAY_MAX_BODY", str(1 * 1024 * 1024)))
RATE_PER_MIN = int(os.environ.get("NEXUS_GATEWAY_RATE_PER_MIN", "60"))
CONNECT_TIMEOUT = float(os.environ.get("NEXUS_GATEWAY_CONNECT_TIMEOUT", "5"))
READ_TIMEOUT = float(os.environ.get("NEXUS_GATEWAY_READ_TIMEOUT", "60"))

# Exact paths allowed (plus prefixes below)
EXACT = {
    "/api/bridge/health",
    "/api/bridge/status",
    "/api/bridge/capabilities",
    "/api/bridge/tasks",
    "/api/bridge/message",
    "/api/status",
    "/api/capabilities",
    "/api/message",
    "/api/task",
}

# Prefix allows: /api/task/..., /api/v1/...
PREFIXES = ("/api/task/", "/api/v1/")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

_rate_lock = threading.Lock()
_rate: Dict[str, Deque[float]] = {}
_log_lock = threading.Lock()


def _ensure_log_dir() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


def _redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in ("authorization", "cookie", "x-api-key", "x-nexus-token"):
            if isinstance(v, str) and len(v) > 8:
                out[k] = v[:4] + "…REDACTED…" + v[-4:]
            else:
                out[k] = "REDACTED"
        else:
            out[k] = v
    return out


def log_event(event: str, **fields) -> None:
    _ensure_log_dir()
    rec = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }
    line = json.dumps(rec, ensure_ascii=False)
    day = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(LOG_DIR, f"gateway-{day}.jsonl")
    with _log_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def allowed_path(path: str) -> bool:
    if not path:
        return False
    # strip query for allow check
    p = path.split("?", 1)[0]
    if p in EXACT:
        return True
    for pref in PREFIXES:
        if p.startswith(pref):
            return True
    return False


def rate_ok(client: str) -> bool:
    now = time.time()
    with _rate_lock:
        q = _rate.setdefault(client, deque())
        while q and (now - q[0]) > 60.0:
            q.popleft()
        if len(q) >= RATE_PER_MIN:
            return False
        q.append(now)
        return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NexusBridgeGateway/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # silence default stderr access log; we use structured logs
        return

    def _client_id(self) -> str:
        return self.client_address[0] if self.client_address else "unknown"

    def _reject(self, code: int, err: str, message: str) -> None:
        body = json.dumps({"success": False, "error": err, "message": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Nexus-Gateway", "allowlist")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return None
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _proxy(self, method: str) -> None:
        raw_path = self.path or "/"
        path_only = urlsplit(raw_path).path or "/"
        client = self._client_id()

        if not allowed_path(path_only):
            log_event("deny_path", method=method, path=path_only, client=client, code=403)
            self._reject(403, "PATH_DENIED", "Path not allowlisted for external bridge access")
            return

        if not rate_ok(client):
            log_event("rate_limited", method=method, path=path_only, client=client, code=429)
            self._reject(429, "RATE_LIMITED", f"Max {RATE_PER_MIN} requests per minute")
            return

        body = self._read_body()
        if body is None:
            log_event("body_too_large", method=method, path=path_only, client=client, code=413)
            self._reject(413, "BODY_TOO_LARGE", f"Max body {MAX_BODY} bytes")
            return

        target = ORIGIN + raw_path
        req_headers = {}
        for k in self.headers.keys():
            if k.lower() in HOP_BY_HOP:
                continue
            req_headers[k] = self.headers.get(k)

        log_event(
            "proxy",
            method=method,
            path=path_only,
            client=client,
            bytes_in=len(body),
            headers=_redact_headers(dict(req_headers)),
        )

        req = urllib.request.Request(target, data=body if method in ("POST", "PUT", "PATCH") else None, method=method)
        for k, v in req_headers.items():
            try:
                req.add_header(k, v)
            except Exception:
                pass

        try:
            with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as resp:
                resp_body = resp.read()
                code = getattr(resp, "status", None) or resp.getcode()
                self.send_response(int(code))
                for k, v in resp.headers.items():
                    if k.lower() in HOP_BY_HOP:
                        continue
                    if k.lower() == "content-length":
                        continue
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(resp_body)))
                self.send_header("X-Nexus-Gateway", "allowlist")
                self.end_headers()
                self.wfile.write(resp_body)
                log_event("proxy_ok", method=method, path=path_only, client=client, code=int(code), bytes_out=len(resp_body))
        except urllib.error.HTTPError as e:
            resp_body = e.read() if e.fp else b""
            self.send_response(int(e.code))
            ctype = e.headers.get("Content-Type") if e.headers else None
            if ctype:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(resp_body)))
            self.send_header("X-Nexus-Gateway", "allowlist")
            self.end_headers()
            if resp_body:
                self.wfile.write(resp_body)
            log_event("proxy_http_error", method=method, path=path_only, client=client, code=int(e.code))
        except Exception as e:
            log_event("proxy_error", method=method, path=path_only, client=client, error=type(e).__name__)
            self._reject(502, "ORIGIN_UNAVAILABLE", "Bridge origin unreachable or timed out")

    def do_GET(self):
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    def do_HEAD(self):
        self._proxy("HEAD")

    def do_OPTIONS(self):
        # Deny non-allowlisted preflight too
        path_only = urlsplit(self.path or "/").path or "/"
        if not allowed_path(path_only):
            self._reject(403, "PATH_DENIED", "Path not allowlisted")
            return
        self.send_response(204)
        self.send_header("X-Nexus-Gateway", "allowlist")
        self.end_headers()


def main() -> int:
    _ensure_log_dir()
    httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    httpd.daemon_threads = True
    log_event("start", host=LISTEN_HOST, port=LISTEN_PORT, origin=ORIGIN, rate=RATE_PER_MIN, max_body=MAX_BODY)
    print(f"NEXUS_GATEWAY_LISTEN={LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    print(f"NEXUS_GATEWAY_ORIGIN={ORIGIN}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log_event("stop")
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
