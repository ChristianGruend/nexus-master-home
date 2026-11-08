"""Handy (Phone-API, NEXUS-OS-Link), Apps, OS-Aktionen und Crew."""
from __future__ import annotations

import traceback

from starlette.routing import Route

from nexus import nexus_os_link, phone_api
from nexus.chat.routes import BOTH, ENGINE, GET, POST, body, coded, peer, qs, reply


def os_link(r):
    """005D NEXUS OS Link (Geräte-Heartbeat / Sync), netzbeschränkt im Modul selbst."""
    try:
        return coded(nexus_os_link.handle(r.method, r.url.path, peer(r), r.headers,
                                          **({"qs": qs(r)} if r.method == "GET" else {"body": body(r), "engine": ENGINE})))
    except Exception:
        traceback.print_exc()
        return reply({"success": False, "error": "NEXUS_OS_LINK_UNAVAILABLE"}, 503)


def phone(r):  # NEXUS Phone 002: Kontakte/Notizen/Kalender/Timer/Gerät
    return coded(phone_api.handle_get(r.url.path, qs(r)) if r.method == "GET" else phone_api.handle_post(r.url.path, body(r)))


def crew_run(r):
    b = body(r)
    sess = ENGINE.get_session(b.get("session_id") or ENGINE.session_id)
    hist = [{"role": m.get("role"), "content": m.get("content", "")}
            for m in sess.get("messages") or [] if m.get("role") in ("user", "assistant", "system")]
    return reply(ENGINE.master.crew.run(ENGINE.master, b.get("id") or b.get("tag") or b.get("agent_id") or "",
                                        b.get("message") or b.get("text") or "", hist))


def app_open(r):  # echt öffnen (native/URL); id | app_id | name | app
    b = body(r)
    aid = b.get("id") or b.get("app_id") or b.get("name") or b.get("app") or ""
    return reply(ENGINE.master.apps.open_info(aid) if b.get("info_only") else ENGINE.master.apps.open(aid))


def _os(r):
    return ENGINE.master.os_actions


routes = [
    *[Route(p, os_link, methods=BOTH) for p in nexus_os_link.PATHS],
    *[Route(p, phone, methods=GET) for p in phone_api.GET_ROUTES],
    *[Route(p, phone, methods=POST) for p in phone_api.POST_ROUTES],
    Route("/api/apps", lambda r: reply(ENGINE.master.apps.list()), methods=GET),
    Route("/api/apps/connect", lambda r: reply(ENGINE.master.apps.connect(body(r).get("id") or body(r).get("app_id") or "")), methods=POST),
    Route("/api/apps/disconnect", lambda r: reply(ENGINE.master.apps.disconnect(body(r).get("id") or body(r).get("app_id") or "")), methods=POST),
    Route("/api/apps/open", app_open, methods=POST),
    Route("/api/crew", lambda r: reply(ENGINE.master.crew.list_agents()), methods=GET),
    Route("/api/crew/active", lambda r: reply(ENGINE.master.crew.get_active()), methods=GET),
    Route("/api/crew/active", lambda r: reply(ENGINE.master.crew.set_active(body(r).get("id") or body(r).get("tag") or "")), methods=POST),
    Route("/api/crew/run", crew_run, methods=POST),
    Route("/api/os/open_url", lambda r: reply(_os(r).open_url(body(r).get("url") or "")), methods=POST),
    Route("/api/os/open_path", lambda r: reply(_os(r).open_path(body(r).get("path") or "", reveal=bool(body(r).get("reveal") or body(r).get("folder")))), methods=POST),
    Route("/api/os/launch_app", lambda r: reply(_os(r).launch_app(body(r).get("name") or body(r).get("app") or "")), methods=POST),
    Route("/api/os/play_media", lambda r: reply(_os(r).play_media(body(r).get("path") or None)), methods=POST),
    Route("/api/os/list_dir", lambda r: reply(_os(r).list_dir_os(body(r).get("path") or "")), methods=POST),
    Route("/api/os/close_app", lambda r: reply(_os(r).close_app(body(r).get("name") or body(r).get("app") or "")), methods=POST),
    Route("/api/os/close_pid", lambda r: reply(_os(r).close_pid(body(r).get("pid"))), methods=POST),
    *[Route(p, lambda r: reply(_os(r).close_opened(body(r).get("url") or None)), methods=POST) for p in ("/api/os/close_opened", "/api/os/close_url")],
]
