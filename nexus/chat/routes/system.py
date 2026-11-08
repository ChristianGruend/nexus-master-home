"""Status/Monitor, NEXUS OS, Projekte/Tasks, Memory, Dateien, Wissen, Ziele und Skills."""
from __future__ import annotations

from starlette.routing import Route

from nexus.chat.routes import ENGINE, GET, POST, arg, body, local, reply


def _int(r, name, default):
    try:
        return int(arg(r, name, str(default)))
    except ValueError:
        return default


def monitor(r):
    from nexus.computer import monitor as mon
    if r.url.path == "/api/system/monitor/history":
        return reply({"success": True, "samples": mon.history_read(_int(r, "n", 60))})
    if r.url.path == "/api/system/alerts":
        data = mon.collect(write_history=False)
        return reply({"success": True, "alerts": data.get("alerts") or [], "timestamp": data.get("timestamp")})
    return reply(mon.collect())


def nexus_os_status(r):
    """v2: Monitor-Schnappschuss; ?refresh=1 prüft sofort, ?e2e=chat startet zusätzlich einen lokalen Chat-E2E."""
    from nexus import nexus_os
    nexus_os.start_monitor(lambda: ENGINE.master.ai)
    force = arg(r, "refresh", "0") in ("1", "true", "yes")
    if arg(r, "e2e") == "chat":
        from nexus.maintenance import block_payload, maintenance
        if maintenance().is_draining():
            return reply(block_payload(), 503)
        with maintenance().work("nexus_os_e2e"):
            nexus_os.run_chat_e2e()
        force = True
    out = dict(nexus_os.collect(provider=ENGINE.master.ai if force else None, force=force))
    try:  # 003A: ALL-Modus-Status (nur lesen; ACTIVE kommt vom Admin-Dienst)
        from nexus.integrations import hermes_allmode
        out["all_mode"] = hermes_allmode.status()
    except Exception as e:
        out["all_mode"] = {"state": "OFF", "error": type(e).__name__}
    return reply(out)


def nexus_os_lite(r):  # nur lesen, erzwingt nie einen Prüfzyklus
    from nexus import nexus_os
    nexus_os.start_monitor(lambda: ENGINE.master.ai)
    return reply(nexus_os.public_view(nexus_os.collect()))


def nexus_os_identity(r):
    from nexus import nexus_os
    return reply({"success": True, **nexus_os.identity(), "config_path": str(nexus_os.CONFIG_PATH)})


def providers(r):
    try:
        out = ENGINE.master.ai.provider_status()
    except Exception as e:
        return reply({"success": False, "error": type(e).__name__})
    return reply({**out, "success": True})


def evidence(r):
    from nexus.master.evidence import capability_snapshot
    return reply({"success": True, **capability_snapshot()})


def orders(r):
    from nexus.master.orders_view import orders_snapshot
    return reply({"success": True, **orders_snapshot()})


def activity(r):
    from nexus.master.activity import get_activity_log
    return reply(get_activity_log().snapshot(limit=_int(r, "limit", 100)))


def files_json(result) -> object:
    """Datei-API-Antwort; geschützte Pfade (Stufe 2.1b) -> 403 statt 200."""
    return reply(result, 403 if isinstance(result, dict) and result.get("error") == "PROTECTED_PATH" else 200)


def memory_delete(r):  # nur lokal, Archiv vor dem Löschen
    if not local(r):
        return reply({"success": False, "error": "LOCAL_ONLY"}, 403)
    res = ENGINE.master.memory.delete(body(r).get("id"))
    return reply(res, 200 if res.get("success") else (404 if res.get("error") == "NOT_FOUND" else 500))


def goal(r):  # 008-P04/P10: Ziel-Planer, Wiederaufnahme
    from nexus.tasks import goal_planner
    if r.url.path.endswith("/resume"):
        return reply(goal_planner.resume_open())
    return reply(goal_planner.run_goal(body(r).get("goal"), dry_run=bool(body(r).get("dry_run"))))


def user_skills(r):  # 008-P10: selbst erstellte Skills (nur SAFE-Tasks via Orchestrator)
    from nexus.skills import registry as sk
    action, b = r.path_params["action"], body(r)
    if action == "draft":
        return reply(sk.draft_user_skill(b.get("name"), b.get("description") or "", b.get("test_params")))
    if action == "approve":
        return reply(sk.approve_user_skill(b.get("name"), b.get("approve") is True))
    if action == "create":
        return reply(sk.create_user_skill(b.get("name"), b.get("steps") or [], b.get("description") or "", b.get("author") or "api"))
    if action == "run":
        return reply(sk.run_user_skill(b.get("name"), b.get("params") or {}))
    if action == "list":
        return reply({"success": True, "skills": sk.list_user_skills()})
    return reply({"success": False, "error": "not_found", "path": r.url.path}, 404)


routes = [
    Route("/api/health", lambda r: reply(ENGINE.health()), methods=GET),
    Route("/api/status", lambda r: reply(ENGINE.status()), methods=GET),
    *[Route(p, monitor, methods=GET) for p in ("/api/system/metrics", "/api/system/monitor",
                                               "/api/system/monitor/history", "/api/system/alerts")],
    Route("/api/nexus-os/status", nexus_os_status, methods=GET),
    Route("/api/nexus-os/status/lite", nexus_os_lite, methods=GET),
    Route("/api/nexus-os/identity", nexus_os_identity, methods=GET),
    Route("/api/nexus-os/providers", providers, methods=GET),
    Route("/api/capabilities/evidence", evidence, methods=GET),
    Route("/api/orders", orders, methods=GET),
    Route("/api/activity", activity, methods=GET),
    Route("/api/projects", lambda r: reply({"success": True, "active": ENGINE.master.projects.active_id, "projects": ENGINE.master.projects.list()}), methods=GET),
    Route("/api/projects", lambda r: reply(ENGINE.master.projects.create(body(r).get("name", "Projekt"), body(r).get("description", ""))), methods=POST),
    Route("/api/projects/update", lambda r: reply(ENGINE.master.projects.update(body(r).get("id"), **{k: body(r).get(k) for k in ("name", "description", "notes", "files")})), methods=POST),
    Route("/api/projects/delete", lambda r: reply(ENGINE.master.projects.delete(body(r).get("id"))), methods=POST),
    Route("/api/projects/active", lambda r: reply(ENGINE.master.projects.set_active(body(r).get("id"))), methods=POST),
    Route("/api/tasks", lambda r: reply({"success": True, "tasks": ENGINE.master.tasks.list(arg(r, "status") or None)}), methods=GET),
    Route("/api/tasks", lambda r: reply(ENGINE.master.tasks.create(body(r).get("title", "Task"), body(r).get("plan"), body(r).get("status", "Active"))), methods=POST),
    Route("/api/tasks/update", lambda r: reply(ENGINE.master.tasks.update(body(r).get("id"), **{k: body(r).get(k) for k in ("title", "status", "plan", "result")})), methods=POST),
    Route("/api/memory", lambda r: reply(ENGINE.master.memory.recall(arg(r, "q"))), methods=GET),
    Route("/api/memory/recall", lambda r: reply(ENGINE.master.memory.recall(body(r).get("query") or body(r).get("q", ""))), methods=POST),
    Route("/api/memory/correct", lambda r: reply(ENGINE.master.memory.correct(body(r).get("id"), body(r).get("text") or "")), methods=POST),
    Route("/api/memory/merk", lambda r: reply(ENGINE.master.memory.merk(body(r).get("text") or body(r).get("message", ""))), methods=POST),
    Route("/api/memory/delete", memory_delete, methods=POST),
    Route("/api/corrections", lambda r: reply({"success": True, "items": ENGINE.master.corrections.list(limit=_int(r, "limit", 40) or 40),
                                               "context": ENGINE.master.corrections.context_snippets()}), methods=GET),
    Route("/api/corrections", lambda r: reply(ENGINE.master.corrections.add(body(r).get("text") or body(r).get("message") or "",
                                                                raw=body(r).get("raw"), kind=body(r).get("kind"))), methods=POST),
    Route("/api/files", lambda r: files_json(ENGINE.master.files.list(arg(r, "path"))), methods=GET),
    Route("/api/files/read", lambda r: files_json(ENGINE.master.files.read(body(r).get("path", ""))), methods=POST),
    Route("/api/files/write", lambda r: files_json(ENGINE.master.files.write(body(r).get("path", ""), body(r).get("content", ""))), methods=POST),
    Route("/api/knowledge/search", lambda r: reply(ENGINE.master.research.search(body(r).get("query") or body(r).get("q", ""))), methods=POST),
    Route("/api/autonomy/goal", goal, methods=POST),
    Route("/api/autonomy/resume", goal, methods=POST),
    Route("/api/skills/user/{action}", user_skills, methods=POST),
]
