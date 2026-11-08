"""Chat, Sitzungen, Shared Room, Partner-Agenten (Hermes/AETHER/GPT) und Assistant-API."""
from __future__ import annotations

import hmac
import json
import os
import re
import traceback

from starlette.responses import StreamingResponse
from starlette.routing import Route

from nexus.chat.engine import IDENTITY_FIELDS, response_identity
from nexus.chat.routes import ENGINE, GET, POST, ROOT, SHARED_ROOM, arg, body, local, peer, reply
from nexus.common import agent_guard

CLOUD_MODES = ("cloud", "openai", "openai_cloud", "eddybangg")


def _load_assistant_token() -> str:
    """Bearer-Token: Env NEXUS_ASSISTANT_TOKEN, sonst workspace/apex/assistant_token.txt (nie committen)."""
    env = (os.environ.get("NEXUS_ASSISTANT_TOKEN") or "").strip()
    if env:
        return env
    try:
        return (ROOT / "workspace" / "apex" / "assistant_token.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


ASSISTANT_TOKEN = _load_assistant_token()
print(f"Assistant-API Auth: Bearer aktiv (Token ...{ASSISTANT_TOKEN[-4:]})" if ASSISTANT_TOKEN else
      "Assistant-API Auth: WARN - kein Token (Env/Datei). /api/assistant -> 401 bis Token gesetzt.", flush=True)


def _mode(b) -> str:
    return str(b.get("mode") or b.get("orchestrator") or "").strip().lower()


def _guard(r):
    """Phase 1 (WP1): Agenten-Endpunkte nur Loopback oder explizit tokenisiert; Ablehnung wird auditiert."""
    ok, why = agent_guard.allow(peer(r), r.headers)
    if ok:
        return None
    try:
        from nexus.terminal.guard import audit
        audit("agent_endpoint_denied", path=r.url.path[:200], ip=peer(r), reason=why)
    except Exception:
        pass
    return reply({"success": False, "error": why}, 403)


def _bad_chat_session(b):
    """Nur Master-Home-Anfragen mit chat_session_id schreiben Partner-Züge; nie eine Hermes-CLI-session_id."""
    if "chat_session_id" in b and not (isinstance(b["chat_session_id"], str) and b["chat_session_id"] in ENGINE.sessions):
        return reply({"success": False, "error": "CHAT_SESSION_NOT_FOUND"}, 404)
    return None


def _partner_response(b: dict, result: dict, partner: str) -> dict:
    if "chat_session_id" in b:
        message = str((b.get("task") if partner == "hermes" else None)
                      or b.get("message") or b.get("task") or b.get("text") or "")
        return ENGINE.record_partner_turn(message, result, b["chat_session_id"], partner)
    return {**result, **response_identity(result, partner)}


def chat(r):
    b = body(r)
    if _mode(b) in CLOUD_MODES and (bad := _bad_chat_session(b)):
        return bad
    from nexus.integrations import hermes_allmode as ham  # 003A: /hermes all modus | /hermes normal
    msg = str(b.get("message") or b.get("text") or "")
    cmd = ham.match_command(msg)
    if cmd:
        src = str(b.get("source") or "chat").lower()
        out = (ham.request_start(source=src, owner=local(r), in_dir=b.get("in_dir"),
                                 query=str(b.get("query")).strip() if b.get("query") else ham.split_start(msg))
               if cmd == "start" else ham.request_end(source=src))
        out.setdefault("session_id", b.get("session_id") or ENGINE.session_id)
        return reply(out)
    if ham.status().get("state") == "ACTIVE" and msg.strip().lower().startswith(("hermes:", "@hermes")) and ham.needs_separate_confirmation(msg):
        ham._log("separate_confirmation_required", task=msg[:300])
        return reply({"success": False, "error": "SEPARATE_CONFIRMATION_REQUIRED", "reply": "Ordner loeschen/irreversible Schritte brauchen eine gesonderte Bestaetigung ueber den Admin-Dienst. Nichts ausgefuehrt.", "session_id": b.get("session_id") or ENGINE.session_id})
    if _mode(b) in CLOUD_MODES:
        from nexus.master.openai_orchestrator import orchestrate
        res = orchestrate(msg)
        out = dict(res) if isinstance(res, dict) else {"success": True, "reply": str(res)}
        out.setdefault("session_id", b.get("session_id") or ENGINE.session_id)
        return reply(_partner_response(b, out, "gpt"))
    return reply(ENGINE.chat(b.get("message") or b.get("text") or "", b.get("session_id"), b.get("project_id"), model=b.get("model")))


def _sse(payload) -> str:
    return f"data: {payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)}\n\n"


def _chat_stream(b: dict):
    """SSE-Ereignisse eines Chat-Zugs. Wird der Generator geschlossen (Client weg), schließt er den
    Engine-Generator sofort (beendet den Ollama-Request) – kein Fehlerereignis, kein Neustart."""
    from nexus.maintenance import maintenance
    rw = "regenerate" if b.get("regenerate") else ("edit" if b.get("edit") else None)  # CHAT04
    gen = ENGINE.chat_stream(b.get("message") or b.get("text") or "", b.get("session_id"), b.get("project_id"),
                             model=b.get("model"), **({"rewrite": rw} if rw else {}))
    try:
        with maintenance().work("chat_stream"):
            for ev in gen:
                if ev.get("type") == "token":
                    yield _sse({"type": "token", "text": ev.get("text", "")})
                elif ev.get("type") == "done":
                    yield _sse({"type": "done", "reply": ev.get("reply", ""), "session_id": ev.get("session_id"),
                                "model": ev.get("model"), "intent": ev.get("intent"), "success": ev.get("success", True),
                                "error": ev.get("error"), "data": ev.get("data"), "plan": ev.get("plan"),
                                **{k: ev[k] for k in IDENTITY_FIELDS if k in ev}})
        yield _sse("[DONE]")
    except Exception as e:
        traceback.print_exc()
        yield _sse({"type": "done", "reply": f"Stream-Fehler: {e}", "session_id": ENGINE.session_id,
                    "model": None, "success": False, "error": str(e)})
        yield _sse("[DONE]")
    finally:
        gen.close()


def chat_stream(r):
    return StreamingResponse(_chat_stream(body(r)), media_type="text/event-stream; charset=utf-8",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def assistant(r):
    """Dünner Alias für externe Assistenten (ChatGPT Custom GPT / Eddie PowerShell), eigener Bearer."""
    got = r.headers.get("Authorization") or ""
    got = got[7:].strip() if got.lower().startswith("bearer ") else ""
    if not (ASSISTANT_TOKEN and got and hmac.compare_digest(got.encode(), ASSISTANT_TOKEN.encode())):
        return reply({"success": False, "error": "unauthorized"}, 401)
    b = body(r)
    msg = str(b.get("message") or b.get("text") or "").strip()
    if not msg:
        return reply({"success": False, "error": "message_required", "reply": "", "source": "assistant_api"}, 400)
    src = b.get("source") or "assistant_api"
    if "NEXUS_API_OK" in msg:  # Handshake
        return reply({"success": True, "reply": "NEXUS_API_OK - Nexus Assistant-API erreichbar.", "source": "assistant_api",
                      "session_id": b.get("session_id") or ENGINE.session_id, "request_source": src})
    res = ENGINE.chat(msg, b.get("session_id"), b.get("project_id"), model=b.get("model"))
    out = {**(res if isinstance(res, dict) else {"success": True, "reply": str(res)}), "source": "assistant_api"}
    if src != "assistant_api":
        out["request_source"] = src
    return reply(out)


def aether_supervisor(r):  # 2026-11-02 Supervisor-MVP
    if denied := _guard(r):
        return denied
    from nexus.integrations.aether_agent import supervise
    b = body(r)
    special = bool(b.get("special_order"))
    if special and not local(r):
        return reply({"success": False, "error": "SPECIAL_ORDER_LOCAL_OWNER_ONLY"}, 403)
    res = supervise(str(b.get("task") or b.get("message") or ""), b.get("approval_id"), special)
    return reply(_partner_response(b, res, "aether"), 200 if res.get("success") else 503)


def aether_agent(r):
    b = body(r)
    if bad := _bad_chat_session(b) or _guard(r):
        return bad
    from nexus.integrations.aether_agent import ask
    res = ask(str(b.get("message") or b.get("task") or b.get("text") or ""))
    return reply(_partner_response(b, res, "aether"), 200 if res.get("success") else 503)


def gpt_tool(r):  # NEXUS GPT Werkzeug über NEXUS-Terminal (Rechtestufen, Erfolgsprüfung)
    if denied := _guard(r):
        return denied
    from nexus.integrations import gpt_local_agent as gla
    b = body(r)
    if str(b.get("command") or "").strip():
        return reply(gla.run_tool(str(b.get("command")), shell=str(b.get("shell") or "session")))
    return reply(gla.tool_ask(str(b.get("task") or b.get("message") or "")))


def gpt_agent(r):
    if denied := _guard(r):
        return denied
    from nexus.integrations.gpt_hermes_bridge import ask_with_hermes
    b = body(r)
    res = ask_with_hermes(str(b.get("message") or b.get("task") or b.get("text") or ""))
    return reply(_partner_response(b, res, "gpt"), 200 if res.get("success") else 409 if res.get("approval_required") else 503)


def hermes_all_confirm(r):  # 003A: nur Nonce prüfen + Anfrage markieren
    from nexus.integrations import hermes_allmode as ham
    if not local(r):
        ham._log("confirm_rejected", reason="NOT_LOCAL")
        return reply({"accepted": False, "error": "NONCE_INVALID"}, 403)
    res = ham.confirm(body(r))
    return reply(res, 200 if res.get("accepted") else 409)


def hermes_agent(r):
    b = body(r)
    if bad := _bad_chat_session(b) or _guard(r):
        return bad
    from nexus.integrations.hermes_agent import ask
    res = ask(str(b.get("task") or b.get("message") or ""))
    return reply(_partner_response(b, res, "hermes"), 200 if res.get("success") else 503)


def hermes_brain(r):
    from nexus.integrations.hermes_brain import HermesBrain
    if r.method == "GET":
        return reply(HermesBrain(ENGINE.master).availability())
    if denied := _guard(r):
        return denied
    b = body(r)
    task = b.get("task") or b.get("message") or b.get("text") or ""
    if not str(task).strip():
        return reply({"success": False, "error": "task_required", "reply": "Feld 'task' ist erforderlich.",
                      "source": "hermes_brain_api"}, 400)
    res = HermesBrain(ENGINE.master).run(str(task), session_messages=b.get("history"), requirements=b.get("requirements"))
    code = 200 if res.get("success") else (207 if res.get("status") in ("PARTIAL", "BLOCKED") else 200)
    return reply({**res, "session_id": b.get("session_id") or ENGINE.session_id, "source": "hermes_brain_api"}, code)


def health_of(module: str):
    def endpoint(r):
        import importlib
        res = importlib.import_module(module).health()
        return reply(res, 200 if res.get("reachable") else 503)
    return endpoint


def orchestrate(r):  # OpenAI cloud orchestrator (Responses API + local Agent Bridge)
    from nexus.master.openai_orchestrator import orchestrate as orch
    b = body(r)
    return reply(orch(str(b.get("message") or b.get("text") or b.get("prompt") or "")))


def ai_config(r):
    from nexus.common import ai_layer
    if r.method == "GET":
        return reply({"success": True, "ok": True, **ai_layer.status(ENGINE.master.llm)})
    b = body(r)
    updates = {k: b.get(k) for k in ("provider", "chat_model", "vision_model", "ollama_url", "keep_alive",
                                     "unload_after_request", "never_pull", "on_demand_only", "memory_fallback", "notes") if k in b}
    if b.get("model") and "chat_model" not in updates:
        updates["chat_model"] = b.get("model")
    if updates.get("chat_model"):
        updates["chat_model_source"] = "ui"
    cfg = ai_layer.save_config(updates) if updates else ai_layer.load_config()
    applied = ai_layer.apply_to_client(ENGINE.master.llm)
    return reply({"success": True, "ok": True, "config": cfg, "applied": applied, "status": ai_layer.status(ENGINE.master.llm)})


def session_rev(r):
    """2026-10-28 SYNC: leichter Änderungs-Stempel für PC <-> Handy (NEXUS OS GPT)."""
    sid = arg(r, "id") or ENGINE.session_id
    s = ENGINE.get_session(sid) or {}
    return reply({"success": True, "id": s.get("id") or sid, "active": ENGINE.session_id,
                  "count": len(s.get("messages") or []), "updated": s.get("updated"), "title": s.get("title")})


def shared_room_events(r):
    sid = arg(r, "session_id", ENGINE.session_id)
    room = SHARED_ROOM.load(sid)
    state = room.get("collaboration") or {}
    run_id = state.get("run_id")
    messages = [m for m in room.get("messages", []) if m.get("source") == "nexus_collaboration" and (m.get("metadata") or {}).get("run_id") == run_id]
    return reply({"success": True, "session_id": sid, "run_id": run_id, "status": state.get("status", "UNKNOWN"),
                  "phase": state.get("phase"), "messages": messages, "events": state.get("events", []), "max_rounds": 2})


def shared_room_message(r):
    b = body(r)
    if str(b.get("sender_type") or b.get("sender") or "human").lower() not in ("human", "eddy"):
        return reply({"success": False, "error": "SERVER_OWNED_AI_IDENTITY"}, 403)
    res = SHARED_ROOM.append(
        session_id=str(b.get("session_id") or ENGINE.session_id),
        sender_type=str(b.get("sender_type") or b.get("sender") or "human"),
        sender_name=str(b.get("sender_name") or b.get("sender") or "Eddy"),
        content=str(b.get("content") or b.get("message") or ""), source="human_input",
        target=b.get("target"), role=str(b.get("role") or "assistant"),
        round_no=int(b.get("round") or 0), parent_message_id=b.get("parent_message_id") or b.get("reply_to"),
        task_id=b.get("task_id"), approval_id=b.get("approval_id"), agent=b.get("agent"),
        provider=b.get("provider"), metadata=b.get("metadata") if isinstance(b.get("metadata"), dict) else {},
        mode=str(b.get("mode") or "user"))
    return reply(res, 200 if res.get("success") else 409)


def dev_orders(r):
    """Alle Aufträge einer Sitzung mit getrenntem Zustand; nur belegte Orchestrator-Ereignisse."""
    sid = arg(r, "session_id")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", sid):
        return reply({"success": False, "error": "session_id required"})
    try:
        room = SHARED_ROOM.load(sid)
    except Exception:
        return reply({"success": False, "error": "session_unavailable"})
    runs = room.get("collaboration_runs") or {}
    if not runs and isinstance(room.get("collaboration"), dict):
        runs = {"legacy": room["collaboration"]}
    out = []
    for state in (s for s in runs.values() if isinstance(s, dict)):
        events = [e for e in state.get("events") or [] if isinstance(e, dict)]
        execution = state.get("execution") or {}
        out.append({"run_id": state.get("run_id"), "goal": str(state.get("goal") or "")[:400],
                    "status": state.get("status"), "phase": state.get("phase"), "error": state.get("error"),
                    "dev": [e for e in events if e.get("kind") == "dev"][-80:],
                    "receipts": [e for e in events if e.get("kind") in ("task", "verification")][-60:],
                    "task_ids": execution.get("task_ids") or [],
                    "superseded_task_ids": execution.get("superseded_task_ids") or [],
                    "preview_url": execution.get("preview_url"), "approval_id": execution.get("approval_id"),
                    "verification": state.get("verification"),
                    "superseded_by": state.get("superseded_by"), "resumes": state.get("resumes"),
                    "updated": max([str(e.get("ts") or "") for e in events] or [""])})
    out.sort(key=lambda o: o["updated"], reverse=True)
    return reply({"success": True, "session_id": sid, "orders": out,
                  "active_run_id": (room.get("collaboration") or {}).get("run_id")})


def _sid(b):
    return b.get("id") or b.get("session_id")


routes = [
    Route("/api/chat", chat, methods=POST),
    Route("/api/chat/stream", chat_stream, methods=POST),
    Route("/api/assistant", assistant, methods=POST),
    Route("/api/orchestrate", orchestrate, methods=POST),
    Route("/api/aether/supervisor", aether_supervisor, methods=POST),
    Route("/api/aether/agent", aether_agent, methods=POST),
    Route("/api/aether/health", health_of("nexus.integrations.aether_agent"), methods=GET),
    Route("/api/gpt/tool", gpt_tool, methods=POST),
    Route("/api/gpt/agent", gpt_agent, methods=POST),
    Route("/api/hermes/all/confirm", hermes_all_confirm, methods=POST),
    Route("/api/hermes/agent", hermes_agent, methods=POST),
    Route("/api/hermes/brain", hermes_brain, methods=["GET", "POST"]),
    Route("/api/hermes/health", health_of("nexus.integrations.hermes_agent"), methods=GET),
    Route("/api/ai/models", ai_config, methods=["GET", "POST"]),
    Route("/api/ai/status", ai_config, methods=GET),
    Route("/api/ai/config", ai_config, methods=POST),
    Route("/api/sessions", lambda r: reply({"success": True, "active": ENGINE.session_id, "sessions": ENGINE.list_sessions()}), methods=GET),
    Route("/api/session", lambda r: reply({"success": True, "session": ENGINE.get_session(arg(r, "id") or None)}), methods=GET),
    Route("/api/session/rev", session_rev, methods=GET),
    Route("/api/sessions/new", lambda r: reply(ENGINE.new_session(body(r).get("title", "Neuer Chat"))), methods=POST),
    Route("/api/sessions/rename", lambda r: reply(ENGINE.rename_session(_sid(body(r)), body(r).get("title", ""))), methods=POST),
    Route("/api/sessions/delete", lambda r: reply(ENGINE.delete_session(_sid(body(r)))), methods=POST),
    Route("/api/sessions/switch", lambda r: reply(ENGINE.switch_session(_sid(body(r)))), methods=POST),
    Route("/api/sessions/export", lambda r: reply(ENGINE.export_session(body(r).get("id"))), methods=POST),
    Route("/api/sessions/summarize", lambda r: reply(ENGINE.summarize_session(body(r).get("id"))), methods=POST),
    Route("/api/shared-room", lambda r: reply(SHARED_ROOM.load(arg(r, "session_id") or arg(r, "id", ENGINE.session_id))), methods=GET),
    Route("/api/shared-room/status", lambda r: reply(SHARED_ROOM.status(arg(r, "session_id") or arg(r, "id", ENGINE.session_id))), methods=GET),
    Route("/api/shared-room/events", shared_room_events, methods=GET),
    Route("/api/shared-room/message", shared_room_message, methods=POST),
    Route("/api/dev/orders", dev_orders, methods=GET),
]
