"""NEXUS-Webserver (Stufe 4.6): Starlette-App, zentrale Schutz-Middleware, Start über uvicorn.

Die Routen liegen fachlich getrennt in ``nexus/chat/routes/``; hier stehen nur App-Fabrik, Middleware und main().
"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
import uuid
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from starlette.applications import Starlette  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from nexus.common import api_auth  # noqa: E402
from nexus.common.config import Config  # noqa: E402

# Pflichtdienst: Start abbrechen, wenn die Bridge ihre Routen nicht registrieren kann – sonst meldet
# /api/health ONLINE, während jede Bridge-Route 404 liefert.
from nexus.bridge import api as bridge_api  # noqa: E402
bridge_api.boot()

_CORS_ORIGINS = frozenset(o.strip() for o in os.environ.get("NEXUS_CORS_ORIGINS", "").split(",") if o.strip())
_MAX_BODY_BYTES = int(os.environ.get("NEXUS_MAX_BODY_BYTES") or 64 * 1024 * 1024)
_ALLOWED_HOSTS = frozenset(h.strip().lower() for h in os.environ.get("NEXUS_ALLOWED_HOSTS", "").split(",") if h.strip())
# Stufe 1.5b: CSP für alle UI-Seiten. 'unsafe-inline' bei Skripten bleibt nötig, solange index.html & Co.
# Inline-<script>-Blöcke und onclick-Attribute haben (Abbau in Stufe 5); blockiert aber fremde Skripte,
# eval, Plugins, fremde Einbettung und Formular-/Base-Umleitung.
_UI_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
           "img-src 'self' data: blob: https:; media-src 'self' blob: https:; font-src 'self' data:; "
           "connect-src 'self' https://nominatim.openstreetmap.org; "
           "frame-src 'self' https://www.openstreetmap.org https://www.youtube.com https://www.youtube-nocookie.com; "
           "worker-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'self'; "
           "form-action 'self'; frame-ancestors 'self'")
# POSTs, die auch während eines Wartungs-Drains angenommen werden (lokale Steuerung, Links, eigene Gates).
_UNGATED = ("/api/live/emit", "/api/live/open", "/api/nexusall/", "/api/studio/", "/api/terminal/", "/api/swarm/run",
            "/api/autobuild/", "/api/nexus-os/", "/api/bridge/maintenance", "/api/v1/bridge/maintenance")
_SERVERS: list = []  # laufende uvicorn-Server (für das geordnete Herunterfahren aus der Wartung)


def _host_allowed(host_header: str, extra: frozenset = frozenset()) -> bool:
    """Schutz gegen DNS-Rebinding: nur Hostnamen, die kein Fremder per öffentlichem DNS kontrollieren kann.

    Erlaubt: leerer Host (Nicht-Browser), IP-Literale, Namen ohne Punkt (localhost, MagicDNS-Kurzname),
    *.ts.net, *.local sowie explizit freigegebene Namen (NEXUS_ALLOWED_HOSTS).
    """
    import ipaddress
    h = (host_header or "").strip().lower()
    if not h:
        return True
    if h.startswith("["):
        name = h[1:h.find("]")] if "]" in h else h[1:]
    else:
        name = h.rsplit(":", 1)[0] if h.count(":") == 1 else h
    name = name.rstrip(".")
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if "." not in name or name.endswith(".ts.net") or name.endswith(".local"):
        return True
    return name in extra


def _cross_site_blocked(headers) -> bool:
    """CSRF-Schutz für POST: Browser-Requests von fremden Seiten ablehnen.

    Native Clients (Handy-App, curl, Agenten) senden weder Origin noch Sec-Fetch-Site
    und bleiben unberührt. Der Loopback-Check allein schützt nicht, weil ein Browser
    auf demselben Rechner ebenfalls von 127.0.0.1 kommt.
    """
    if headers.get("Sec-Fetch-Site", "") == "cross-site":
        return True
    origin = headers.get("Origin")
    if not origin or origin in _CORS_ORIGINS:
        return False
    if origin == "null":
        return True
    return urlparse(origin).netloc.lower() != (headers.get("Host") or "").lower()


def _deny(code: int, error: str, **extra) -> JSONResponse:
    return JSONResponse({"success": False, "error": error, **extra}, code)


def _audit(event: str, **fields) -> None:
    try:
        from nexus.terminal.guard import audit
        audit(event, **fields)
    except Exception:
        pass


async def _guard(request, call_next):
    """Für jede Anfrage: Host-Allowlist, Zugang (deny by default), Cross-Site, Body-Limit/JSON,
    Wartungs-Gate, generische Fehler mit Request-ID und Sicherheits-Header."""
    from nexus.chat import routes
    from nexus.maintenance import MaintenanceActive, block_payload, maintenance
    path, ip, h = request.url.path, routes.peer(request), request.headers
    if not _host_allowed(h.get("host", ""), _ALLOWED_HOSTS):
        return _deny(421, "HOST_NOT_ALLOWED")
    ok, why = api_auth.check(path, ip, h)  # Loopback nur über die Peer-Adresse (uvicorn ohne proxy_headers)
    if not ok:
        return _deny(403 if why == "CROSS_SITE" else 401, why, login="/login")
    post = request.method == "POST"
    if post and _cross_site_blocked(h):
        _audit("cross_site_post_denied", path=path[:200], origin=str(h.get("Origin"))[:200])
        return _deny(403, "CROSS_SITE_DENIED")
    try:  # NEXUS OS live link evidence (memory only): letzte Anfrage je Client-IP
        from nexus import nexus_os
        nexus_os.note_client(ip, path)
    except Exception:
        pass
    gate = nullcontext()
    if post:
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > _MAX_BODY_BYTES:
                return _deny(413, "BODY_TOO_LARGE")
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            return _deny(400, "INVALID_JSON")
        if not isinstance(data, dict):
            return _deny(400, "JSON_OBJECT_REQUIRED")
        request.state.body = data
        routes.live_request(path, data)
        pc_confirm = path == "/api/voice/confirm" and str(data.get("id") or "").startswith("pc-")
        if not (path.startswith(_UNGATED) or pc_confirm):
            if maintenance().is_draining():  # Drain: keine neuen Aufträge, Laufendes darf auslaufen
                return JSONResponse(block_payload(), 503)
            gate = maintenance().work("http_post")
    try:
        with gate:
            response = await call_next(request)
    except MaintenanceActive:
        response = JSONResponse(block_payload(), 503)
    except Exception:
        rid = uuid.uuid4().hex[:12]
        print(f"[{rid}] {request.method} {path} fehlgeschlagen:", file=sys.stderr, flush=True)
        traceback.print_exc()
        response = JSONResponse({"success": False, "error": "INTERNAL_ERROR", "request_id": rid}, 500)
    out = response.headers
    ctype = out.get("content-type", "")
    out.setdefault("Referrer-Policy", "no-referrer")
    if ctype.startswith("text/html"):  # kein veraltetes Master Home aus dem Browser-Cache; CSP auf UI-Seiten
        out.setdefault("Cache-Control", "no-cache")
        out.setdefault("Content-Security-Policy", _UI_CSP)
    elif not ctype.startswith(("image/", "video/", "audio/")):
        out.setdefault("X-Content-Type-Options", "nosniff")
    return response


def _not_found(request, exc):
    return JSONResponse({"success": False, "error": "not_found", "path": request.url.path}, 404)


def create_app() -> Starlette:
    from nexus.chat.routes import bridge, chat, devices, media, system, terminal, ui
    return Starlette(
        routes=[*ui.routes, *chat.routes, *terminal.routes, *system.routes, *media.routes, *devices.routes,
                *bridge.routes],  # bridge zuletzt: Sammelroute
        middleware=[Middleware(CORSMiddleware, allow_origins=sorted(_CORS_ORIGINS), allow_methods=["GET", "POST", "OPTIONS"],
                               allow_headers=["Content-Type", "Authorization"]),
                    Middleware(BaseHTTPMiddleware, dispatch=_guard)],
        exception_handlers={404: _not_found, 405: _not_found})


def _start(name: str, module: str, fn: str, *args) -> None:
    """Hintergrunddienst starten; ein Fehler darf den Server nicht aufhalten."""
    try:
        import importlib
        getattr(importlib.import_module(module), fn)(*args)
        print(f"{name} started", flush=True)
    except Exception as e:
        print(f"{name} not started: {type(e).__name__}", flush=True)


def main() -> None:
    import uvicorn
    from nexus.chat.routes import ENGINE
    from nexus.common.paths import ensure_dirs

    ensure_dirs()
    app = create_app()
    # Peer-Adresse nie aus X-Forwarded-For; offene Streams (SSE) halten das Herunterfahren höchstens 3 s auf
    opts = {"proxy_headers": False, "server_header": False, "timeout_graceful_shutdown": 3}
    server = uvicorn.Server(uvicorn.Config(app, host=Config.HOST, port=Config.PORT, **opts))
    _SERVERS[:] = [server]
    print(f"NEXUS AI Master {Config.VERSION} on http://{Config.HOST}:{Config.PORT}/", flush=True)
    print(f"UI: http://127.0.0.1:{Config.PORT}/  Mobile: http://127.0.0.1:{Config.PORT}/m", flush=True)

    cert, key = (p if p.is_absolute() else (ROOT / p).resolve() for p in (Path(Config.SSL_CERT), Path(Config.SSL_KEY)))
    if cert.is_file() and key.is_file():
        https = uvicorn.Server(uvicorn.Config(app, host=Config.HOST, port=Config.SSL_PORT, ssl_certfile=str(cert),
                                              ssl_keyfile=str(key), **opts))
        _SERVERS.append(https)
        threading.Thread(target=https.run, name="nexus-https", daemon=True).start()
        print(f"HTTPS: https://127.0.0.1:{Config.SSL_PORT}/m  (cert={cert})", flush=True)
    else:
        print(f"HTTPS skipped (missing cert/key: {cert} / {key})", flush=True)

    if os.environ.get("NEXUS_ADB_WATCHDOG", "1") != "0":  # NEXUS 005D: ADB-Reconnect-Watchdog (30 s)
        _start("ADB watchdog", "nexus.phone_actions", "start_watchdog", 30.0)
    # Lokale Wartung (/api/bridge/maintenance/shutdown, loopback-only) beendet alle Server geordnet.
    try:
        from nexus.maintenance import maintenance
        maintenance().set_shutdown_callback(lambda: [setattr(s, "should_exit", True) for s in _SERVERS])
    except Exception as e:
        print(f"Maintenance gate not registered: {type(e).__name__}", flush=True)
    _start("NEXUS OS status monitor", "nexus.nexus_os", "start_monitor", lambda: ENGINE.master.ai)
    if os.environ.get("NEXUS_AUTOBUILD", "0") == "1":  # Selbstbau nur bei NEXUS_AUTOBUILD=1; Stop: workspace/autobuild/STOP
        from nexus.autobuild.loop import STOP_FILE, get_autobuild
        if not STOP_FILE.exists():
            get_autobuild().start()
    _start("Aether shell audit watcher", "nexus.integrations.aether_supervisor", "start_shell_audit_watch", 5.0)

    server.run()
    print("shutdown", flush=True)


if __name__ == "__main__":
    main()
