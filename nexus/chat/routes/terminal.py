"""NEXUS-Terminal, Shell/Code, Live-Monitor, Swarm und Autobuild (Schreibendes nur lokal)."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path

from starlette.responses import StreamingResponse
from starlette.routing import Route

from nexus.chat.routes import ENGINE, GET, POST, ROOT, arg, body, loopback_only, reply
from nexus.common import api_auth


def _live_stream(r, fp=None):
    """SSE: tailt workspace/audit/live_events.jsonl; id = Byte-Offset (Reconnect ohne Duplikate)."""
    from nexus.common import live_events
    fp = fp or live_events.LIVE_FILE
    size = fp.stat().st_size if fp.exists() else 0
    last = (r.headers.get("Last-Event-ID") or "").strip()
    if last.isdigit() and int(last) <= size:
        pos = int(last)
    else:
        n = max(0, min(int(arg(r, "backlog", "150")), 1000))
        pos = size
        if n and size:
            with fp.open("rb") as f:
                f.seek(max(0, size - 400_000))
                pos = size - sum(len(x) for x in f.read().splitlines(keepends=True)[-n:])

    async def events():  # async: Starlette bricht bei Verbindungsende/Shutdown sofort ab
        nonlocal pos
        idle = 0.0
        while True:
            try:
                size = fp.stat().st_size
            except FileNotFoundError:
                size = 0
            if size < pos:
                pos = 0  # rotiert
            out = b""
            if size > pos:
                with fp.open("rb") as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
                for ln in chunk[:chunk.rfind(b"\n") + 1].splitlines(keepends=True):
                    pos += len(ln)
                    if ln.strip():
                        out += b"id: " + str(pos).encode() + b"\ndata: " + ln.strip() + b"\n\n"
            idle = 0.0 if out else idle + 0.5
            if idle >= 15:
                out, idle = b": ping\n\n", 0.0
            if out:
                yield out
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream; charset=utf-8", headers={"Cache-Control": "no-cache"})


def live_config(r):  # von Hermes per Bridge erstellt (Abschlusstest)
    cf = ROOT / "workspace" / "projects" / "hermes" / "live_monitor_agents.json"
    try:
        return reply({"success": True, "source": str(cf), **json.loads(cf.read_text(encoding="utf-8"))})
    except Exception:
        return reply({"success": False, "error": "config_missing"})


def live_emit(r):
    from nexus.common import live_events
    if denied := loopback_only(r, "loopback_only"):
        return denied
    b = body(r)
    rec = live_events.emit(str(b.get("agent") or "Hermes-PS"), str(b.get("kind") or "info"), b.get("text") or "",
                           target=b.get("target"), ok=b.get("ok"), data=b.get("data"))
    return reply({"success": rec is not None, "event": rec})


def terminal_get(r):
    from nexus.terminal import guard, service
    action = r.path_params["action"]
    if action == "stream":
        return _live_stream(r, service.LOG_FILE)
    if action == "history":
        return reply({"success": True, "events": service.history(int(arg(r, "limit", "300")), agent=arg(r, "agent") or None,
                                                                 run_id=arg(r, "run") or None)})
    if action == "state":
        return reply({"success": True, "running": service.active_runs(), "approvals": guard.pending_approvals(),
                      "levels": guard.agent_levels()})
    if action == "audit":
        return reply({"success": True, "audit": guard.audit_tail(int(arg(r, "limit", "200")))})
    if action == "sessions":
        return reply({"success": True, "pwsh": service.pwsh_path(), "sessions": service.sessions()})
    return reply({"success": False, "error": "not_found", "path": r.url.path}, 404)


def terminal_post(r):
    """NEXUS-Terminal (20261031), nur lokal. Die Rechtestufe kommt nie aus dem Body."""
    if denied := loopback_only(r):
        return denied
    from nexus.terminal import guard, service
    action, b = r.path_params["action"], body(r)
    if action == "run":
        cmd = str(b.get("command") or "")
        if not cmd.strip():
            return reply({"success": False, "error": "command required"}, 400)
        from nexus.safety import dispatch
        res = dispatch.shell_execute(cmd, agent=str(b.get("agent") or "Eddie"), shell=str(b.get("shell") or "session"),
                                     cwd=b.get("cwd") or None, timeout=b.get("timeout"), wait=b.get("wait", False) is not False,
                                     approval_id=b.get("approval_id") or None, allow_empty=b.get("allow_empty") is True)
        return reply(res, {"BLOCKED": 403, "LEVEL_DENIED": 403, "APPROVAL_REQUIRED": 202}.get(str(res.get("status")), 200))
    if action == "zocker":
        # Zocker-Modus: NEXUS abschalten. Skript per Env, Default neben dem Repo (WoZiPC: G:\meine-ki\zocker_bombe.ps1).
        if os.name != "nt":
            return reply({"success": False, "error": "WINDOWS_ONLY"}, 501)
        script = Path(os.environ.get("NEXUS_ZOCKER_SCRIPT") or (ROOT.parent / "zocker_bombe.ps1"))
        if not script.is_file():
            return reply({"success": False, "error": "ZOCKER_SCRIPT_MISSING"}, 404)
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], creationflags=0x08000000)
        return reply({"success": True})
    if action == "cancel":
        return reply(service.cancel(str(b.get("run") or ""), by=str(b.get("by") or "Eddie-UI")))
    if action == "approve":
        # nur aus dem Browser-UI (same-origin fetch mit UI-Header + HttpOnly-Cookie), nicht per Agent-API
        if not api_auth.human_approval(r.headers)[0]:
            guard.audit("approval_rejected_not_ui", approval_id=str(b.get("approval_id")), ua=str(r.headers.get("User-Agent"))[:120])
            return reply({"success": False, "error": "NUR_EDDIE_UI"}, 403)
        return reply(guard.decide(str(b.get("approval_id") or ""), b.get("approve", True) is not False))
    if action == "reset":
        guard.audit("session_reset", agent=str(b.get("agent") or "Eddie"))
        return reply(service.reset_session(str(b.get("agent") or "Eddie")))
    return reply({"success": False, "error": "unknown"}, 404)


def swarm_run(r):  # NEXUS Swarm AI (20261104), nur lokal; Agent/Rechtestufe kommt aus permissions.json
    if denied := loopback_only(r):
        return denied
    from nexus.swarm.core import get_master
    b = body(r)
    if not str(b.get("task") or "").strip():
        return reply({"success": False, "error": "task required"}, 400)
    s = get_master().submit(str(b["task"]), str(b.get("mode") or "pipeline"), str(b.get("agent") or "NEXUS"))
    return reply({"success": True, "run_id": s["id"], "status": s["status"], "mode": s["mode"]}, 202)


def swarm_get(r):
    from nexus.swarm.core import get_master
    run = get_master().get(r.path_params["run_id"])
    return reply({"success": True, "run": run} if run else {"success": False, "error": "not_found"}, 200 if run else 404)


def autobuild_post(r):  # Selbstbau-Engine (20261028), nur lokal
    if denied := loopback_only(r):
        return denied
    from nexus.autobuild.loop import get_autobuild
    ab, action, b = get_autobuild(), r.path_params["action"], body(r)
    if action in ("start", "stop"):
        return reply(getattr(ab, action)())
    if action == "run":
        if ab.running_cycle:
            return reply({"success": False, "error": "CYCLE_RUNNING"}, 409)
        threading.Thread(target=ab.cycle, kwargs={"item_id": b.get("item"), "full_suite": b.get("full_suite", True) is not False},
                         name="nexus-autobuild-run", daemon=True).start()
        return reply({"success": True, "started": True}, 202)
    return reply({"success": False, "error": "unknown"}, 404)


def shell_run(r):  # Shell / PowerShell (echt) — timeout/cwd/build_mode für npm/vite
    b = body(r)
    return reply(ENGINE.master.shell.run(b.get("command") or b.get("cmd") or "", shell=b.get("shell") or "auto",
                                         timeout=b.get("timeout"), cwd=b.get("cwd"),
                                         build_mode=bool(b.get("build") or b.get("build_mode"))))


def shell_script(r):
    b = body(r)
    return reply(ENGINE.master.shell.run_script(b.get("path") or b.get("script") or "", args=b.get("args"),
                                                timeout=b.get("timeout"), cwd=b.get("cwd")))


def _live_events(r):
    from nexus.common import live_events
    return reply({"success": True, "events": live_events.tail(int(arg(r, "limit", "200")))})


def _live_open(r):
    from nexus.common import live_events
    return reply({"success": True, **live_events.ensure_monitor(force=True)})


def _autobuild_status(r):
    from nexus.autobuild.loop import get_autobuild
    return reply(get_autobuild().status())


def _swarm_status(r):
    from nexus.swarm.core import get_master
    return reply(get_master().status())


routes = [
    Route("/api/terminal/{action}", terminal_get, methods=GET),
    Route("/api/terminal/{action}", terminal_post, methods=POST),
    Route("/api/shell/run", shell_run, methods=POST),
    Route("/api/shell/run_script", shell_script, methods=POST),
    Route("/api/code/run", lambda r: reply(ENGINE.master.code.run(body(r).get("code") or body(r).get("source", ""))), methods=POST),
    Route("/api/live/events", _live_events, methods=GET),
    Route("/api/live/config", live_config, methods=GET),
    Route("/api/live/stream", _live_stream, methods=GET),
    Route("/api/live/emit", live_emit, methods=POST),
    Route("/api/live/open", _live_open, methods=POST),
    Route("/api/swarm/status", _swarm_status, methods=GET),
    Route("/api/swarm/runs/{run_id}", swarm_get, methods=GET),
    Route("/api/swarm/run", swarm_run, methods=POST),
    Route("/api/autobuild/status", _autobuild_status, methods=GET),
    Route("/api/autobuild/{action}", autobuild_post, methods=POST),
]
