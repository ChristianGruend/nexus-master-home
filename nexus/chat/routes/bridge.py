"""Agent Bridge, Wartung (drain), Automationen und Multimodal – Module mit der alten Handler-Schnittstelle.

Muss als letztes Routenmodul eingebunden werden: die Sammelroute bedient alles, was keine eigene Route hat,
und antwortet sonst mit 404 not_found.
"""
from __future__ import annotations

from starlette.routing import Route

from nexus.bridge import api as bridge_api
from nexus.chat.routes import BOTH, ENGINE, body, peer, qs, reply

try:  # Controlled Automations (LOCAL only)
    from nexus.automation import api as automation_api
except Exception as e:
    automation_api = None
    print(f"Automation: import failed: {e}", flush=True)
try:  # Multimodal Layer (LOCAL only, ehrliche Status)
    from nexus.multimodal import api as multimodal_api
except Exception as e:
    multimodal_api = None
    print(f"Multimodal: import failed: {e}", flush=True)


class Compat:
    """Request-Sicht für Module, die per handler._json()/_bytes() antworten (bridge, automation, multimodal, nexusall)."""

    def __init__(self, r):
        self.headers, self.client_address, self.command = r.headers, (peer(r), 0), r.method
        self.response = None

    def _json(self, code, payload):
        self.response = reply(payload, code)

    def _bytes(self, code, data, ctype):
        from starlette.responses import Response
        self.response = Response(data, code, media_type=ctype)


def fallback(r):
    """Bridge-Wartung (Bearer + Loopback im Modul), Automationen, Multimodal, Agent Bridge v1 – sonst 404."""
    path, h, get = r.url.path, Compat(r), r.method in ("GET", "HEAD")
    if bridge_api.is_maintenance_path(path):
        bridge_api.handle_maintenance_get(h, path, qs(r)) if get else bridge_api.handle_maintenance(h, path, body(r))
    elif automation_api and (automation_api.is_automation_get if get else automation_api.is_automation_post)(path):
        automation_api.handle_get(h, path, qs(r)) if get else automation_api.handle_post(h, path, body(r))
    elif multimodal_api and multimodal_api.is_multimodal_get(path):
        (multimodal_api.handle_get(h, path, qs(r), master=ENGINE.master) if get
         else multimodal_api.handle_post(h, path, body(r), master=ENGINE.master))
    elif (bridge_api.is_bridge_get if get else bridge_api.is_bridge_post)(path):
        bridge_api.handle_get(h, path, qs(r)) if get else bridge_api.handle_post(h, path, body(r))
    return h.response or reply({"success": False, "error": "not_found", "path": path}, 404)


routes = [Route("/{path:path}", fallback, methods=BOTH)]
