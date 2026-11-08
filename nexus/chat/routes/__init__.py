"""Gemeinsames für die Routenmodule (Stufe 4.6): Engine-Instanzen und kleine Request-/Antwort-Helfer.

Endpunkte sind normale ``def``-Funktionen; Starlette führt sie im Threadpool aus (der Kern blockiert).
Zugang, Host-Allowlist, Body-Limit/JSON und Fehlerantworten erledigt die Middleware in ``server.py``.
"""
from __future__ import annotations

import contextvars
import json
from pathlib import Path
from urllib.parse import parse_qs

from starlette.responses import FileResponse, Response

from nexus.chat.engine import NexusEngine
from nexus.chat.shared_room import SharedRoom
from nexus.common import agent_guard
from nexus.common.paths import STATIC_DIR

ROOT = Path(__file__).resolve().parents[3]
ENGINE = NexusEngine()
SHARED_ROOM = SharedRoom()
GET, POST, BOTH = ["GET"], ["POST"], ["GET", "POST"]

# Live-Monitor: Agent-Aufträge (POST auf diese Präfixe) und ihre Antwort sichtbar machen.
LIVE_POST = {"/api/hermes": "Hermes", "/api/crew": "Crew", "/api/aether": "Hermes", "/api/chat": "NEXUS",
             "/api/gpt": "NEXUS-GPT"}
_LIVE = contextvars.ContextVar("nexus_live_source", default=None)


def live_request(path: str, body: dict) -> None:
    """Von der Middleware aufgerufen: Auftrag melden; die erste reply() dieses Requests meldet die Antwort."""
    src = next((v for k, v in LIVE_POST.items() if path.startswith(k)), None)
    if not src:
        return
    _LIVE.set(src)
    try:
        from nexus.common import live_events
        live_events.emit("NEXUS", "auftrag", body.get("task") or body.get("message") or body.get("text") or path,
                         target=src, data={"endpoint": path})
    except Exception:
        pass


def reply(payload, code: int = 200, headers: dict | None = None) -> Response:
    """JSON-Antwort wie bisher (ensure_ascii=False, NaN erlaubt)."""
    src = _LIVE.get()
    if src and isinstance(payload, dict):
        _LIVE.set(None)
        try:
            from nexus.common import live_events
            live_events.emit(src, "antwort",
                             f"HTTP {code}: {payload.get('reply') or payload.get('status') or payload.get('error') or ''}",
                             target="NEXUS", ok=bool(payload.get("success")),
                             data={k: payload.get(k) for k in ("status", "error", "run_id", "agent") if payload.get(k)})
        except Exception:
            pass
    return Response(json.dumps(payload, ensure_ascii=False), code, headers, "application/json; charset=utf-8")


def coded(result) -> Response:
    """Für Module, die (status, payload) liefern."""
    return reply(result[1], result[0])


def body(r) -> dict:
    """Von der Middleware gelesener und geprüfter JSON-Body (immer ein dict)."""
    return getattr(r.state, "body", {})


def arg(r, name: str, default: str = "") -> str:
    return r.query_params.get(name) or default


def qs(r) -> dict:
    """Query im alten parse_qs-Format für Module mit dieser Schnittstelle."""
    return parse_qs(r.url.query)


def peer(r) -> str:
    """Peer-Adresse der TCP-Verbindung (uvicorn läuft ohne proxy_headers, nie aus Headern)."""
    return r.client.host if r.client else ""


def local(r) -> bool:
    """Loopback ohne Proxy-/Tailscale-Header."""
    return agent_guard.is_loopback(peer(r), r.headers)


def loopback_only(r, error: str = "LOOPBACK_ONLY") -> Response | None:
    return None if local(r) else reply({"success": False, "error": error}, 403)


def page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, media_type="text/html; charset=utf-8")


def is_within(root: Path, candidate: Path) -> bool:
    """True, wenn candidate (aufgelöst) gleich root oder darunter liegt. Folgt Symlinks."""
    try:
        return candidate.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False
