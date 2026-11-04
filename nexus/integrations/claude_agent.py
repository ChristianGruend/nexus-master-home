"""Claude agent loop with NEXUS tools, approvals, budget and background tasks.

Claude never gets a private execution path. Every action goes through
nexus.master.tools.run_one, which uses the same shell, file, OS, desktop,
phone and calendar implementations as NEXUS. Irreversible tools ask for a
local terminal approval and can be resumed after the approval is granted.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.common.claude_client import (
    ClaudeClient,
    configured_max_tool_rounds,
    default_client,
    mask_secret,
)
from nexus.common.paths import WORKSPACE, ensure_dirs
from nexus.master.tools import run_one

AGENT = "Claude"
TASK_DIR = WORKSPACE / "state" / "claude_tasks"
_TASK_ID = re.compile(r"^claude_[A-Za-z0-9]{8,24}$")
_LOCK = threading.RLock()
_RUNNING: dict[str, threading.Event] = {}
_MAX_STORED_MESSAGES = 40
_MAX_TEXT = 20000


SYSTEM_PROMPT = (
    "Du bist Claude, vollstaendig in NEXUS Master Home integriert. "
    "Du arbeitest auf dem echten Desktop des Nutzers (Windows oder Linux) und hast dieselben "
    "Werkzeuge und Rechte wie NEXUS. Antworte auf Deutsch, direkt und knapp. "
    "Nutze echte Werkzeuge statt zu simulieren. Erfinde niemals Ausgaben, Dateien, Pfade, "
    "Task-IDs oder Erfolge. Ein Erfolg darf nur gemeldet werden, wenn ein Werkzeug ihn belegt. "
    "Bei fehlendem Recht, fehlender Freigabe oder fehlender Hardware sagst du klar, was fehlt. "
    "Irreversible Aktionen (Klick, Tastatur, Senden, Schreiben, Loeschen, HTTP-Aenderungen) "
    "werden vom System freigabepflichtig gemacht; fordere keine Umgehung. "
    "Arbeite mehrstufig: Plan -> Werkzeug -> Ergebnis pruefen -> naechster Schritt. "
    "Wenn ein Werkzeug APPROVAL_REQUIRED liefert, stoppe und nenne die approval_id. "
    "Dateien und Shell bleiben im NEXUS-Projektroot, ausser ein Werkzeug erlaubt ausdruecklich mehr. "
    "Keine Secrets in Antworten, Logs oder Dateien schreiben."
)


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [],
            "additionalProperties": False}


def _tool(name: str, description: str, properties: dict[str, Any],
          required: list[str] | None = None) -> dict[str, Any]:
    return {"name": name, "description": description,
            "input_schema": _schema(properties, required)}


TOOLS: list[dict[str, Any]] = [
    _tool("shell", "Echten Shell-/PowerShell-/Bash-Befehl im Projektroot ausfuehren.",
          {"command": {"type": "string"}, "shell": {"type": "string",
           "enum": ["auto", "bash", "powershell", "cmd", "python"]},
           "cwd": {"type": "string"}, "timeout": {"type": "integer"},
           "approval_id": {"type": "string"}}, ["command"]),
    _tool("run_script", "Skript unter dem Projektroot ausfuehren.",
          {"path": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
           "cwd": {"type": "string"}, "timeout": {"type": "integer"},
           "approval_id": {"type": "string"}}, ["path"]),
    _tool("python_exec", "Python-Code im Projektroot ausfuehren.",
          {"code": {"type": "string"}}, ["code"]),
    _tool("read_file", "Datei im Projektroot lesen.",
          {"path": {"type": "string"}}, ["path"]),
    _tool("write_file", "Datei im Projektroot schreiben.",
          {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _tool("list_dir", "Verzeichnis im Projektroot auflisten.",
          {"path": {"type": "string"}}, ["path"]),
    _tool("status", "Systemstatus abrufen.", {}),
    _tool("system_monitor", "Systemmetriken abrufen oder eine Messfrage beantworten.",
          {"query": {"type": "string"}}),
    _tool("open_url", "URL im Standardbrowser oeffnen.", {"url": {"type": "string"}}, ["url"]),
    _tool("open_path", "Datei oder Ordner mit der Standard-App oeffnen.",
          {"path": {"type": "string"}, "reveal": {"type": "boolean"}}, ["path"]),
    _tool("launch_app", "Installierte Anwendung starten.",
          {"name": {"type": "string"}}, ["name"]),
    _tool("play_media", "Mediendatei oder Standardplayer starten.",
          {"path": {"type": "string"}}),
    _tool("list_dir_os", "Verzeichnis ausserhalb des Projektroots nur lesen.",
          {"path": {"type": "string"}}, ["path"]),
    _tool("close_opened", "Von NEXUS geoeffnete Browser-Seiten schliessen.",
          {"url": {"type": "string"}}),
    _tool("web_search", "Live-Websuche ausfuehren.", {"query": {"type": "string"}}, ["query"]),
    _tool("youtube_search", "YouTube durchsuchen.", {"query": {"type": "string"}}, ["query"]),
    _tool("youtube_play", "YouTube-Suche starten und erstes Ergebnis abspielen.",
          {"query": {"type": "string"}}, ["query"]),
    _tool("volume", "Systemlautstaerke aendern.",
          {"action": {"type": "string", "enum": ["up", "down", "mute", "unmute"]},
           "steps": {"type": "integer"}}, ["action"]),
    _tool("github", "GitHub-CLI-Aktion ausfuehren.",
          {"command": {"type": "string"}, "cwd": {"type": "string"},
           "approval_id": {"type": "string"}}, ["command"]),
    _tool("screenshot", "Bildschirmfoto aufnehmen und als Datei speichern.",
          {"path": {"type": "string"},
           "region": {"type": "array", "items": {"type": "integer"}}}),
    _tool("screen_read", "Bildschirm aufnehmen und mit dem NEXUS-Visionmodell als Text beschreiben.",
          {"prompt": {"type": "string"}}),
    _tool("screen_size", "Bildschirmgroesse lesen.", {}),
    _tool("pointer_position", "Aktuelle Mausposition lesen.", {}),
    _tool("mouse_move", "Mauszeiger bewegen.",
          {"x": {"type": "integer"}, "y": {"type": "integer"},
           "duration": {"type": "number"}, "approval_id": {"type": "string"}}, ["x", "y"]),
    _tool("mouse_click", "Mit der Maus klicken (Freigabe noetig).",
          {"x": {"type": "integer"}, "y": {"type": "integer"},
           "button": {"type": "string", "enum": ["left", "right", "middle"]},
           "clicks": {"type": "integer"}, "interval": {"type": "number"},
           "approval_id": {"type": "string"}}, ["x", "y"]),
    _tool("mouse_scroll", "Mausrad scrollen.",
          {"amount": {"type": "integer"}, "x": {"type": "integer"}, "y": {"type": "integer"},
           "approval_id": {"type": "string"}}, ["amount"]),
    _tool("keyboard_type", "Text ueber die Tastatur eingeben (Freigabe noetig).",
          {"text": {"type": "string"}, "interval": {"type": "number"},
           "approval_id": {"type": "string"}}, ["text"]),
    _tool("keyboard_hotkey", "Tastenkombination senden (Freigabe noetig).",
          {"keys": {"type": "array", "items": {"type": "string"}},
           "approval_id": {"type": "string"}}, ["keys"]),
    _tool("media_key", "Globale Medien-Taste senden (Play/Pause/Next/Prev/Stop/Mute).",
          {"action": {"type": "string", "enum": ["playpause", "next", "prev", "stop", "mute"]},
           "approval_id": {"type": "string"}}, ["action"]),
    _tool("clipboard", "Zwischenablage lesen oder schreiben.",
          {"action": {"type": "string", "enum": ["get", "set"]}, "text": {"type": "string"}},
          ["action"]),
    _tool("calendar_list", "Lokale NEXUS-Termine auflisten.",
          {"from": {"type": "string"}, "to": {"type": "string"}}),
    _tool("calendar_add", "Lokalen NEXUS-Termin anlegen oder aktualisieren.",
          {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
           "note": {"type": "string"}, "id": {"type": "string"}}, ["title", "start"]),
    _tool("calendar_delete", "Lokalen NEXUS-Termin loeschen.", {"id": {"type": "string"}}, ["id"]),
    _tool("http_request", "Echte HTTP(S)-Anfrage senden. Aenderungen brauchen Freigabe.",
          {"method": {"type": "string", "enum": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]},
           "url": {"type": "string"}, "headers": {"type": "object"},
           "body": {"type": "string"}, "json": {}, "timeout": {"type": "number"},
           "approval_id": {"type": "string"}}, ["url"]),
    _tool("whatsapp_read", "Sichtbare WhatsApp-Texte vom verbundenen Handy lesen.",
          {"limit": {"type": "integer"}}),
    _tool("whatsapp_send", "WhatsApp-Nachricht senden (Freigabe noetig).",
          {"number": {"type": "string"}, "contact": {"type": "string"},
           "text": {"type": "string"}, "recipient": {"type": "string"},
           "approval_id": {"type": "string"}}, ["text"]),
    _tool("whatsapp_open", "WhatsApp-Chat mit vorausgefuelltem Text oeffnen.",
          {"number": {"type": "string"}, "contact": {"type": "string"},
           "text": {"type": "string"}, "recipient": {"type": "string"}}),
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _scrub(text: Any) -> str:
    value = mask_secret(text)
    try:
        from nexus.terminal.service import mask
        value = mask(value)
    except Exception:
        pass
    return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + "...[gekuerzt]"


def _scrub_obj(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[zu_tief]"
    if isinstance(value, str):
        return _scrub(value)
    if isinstance(value, dict):
        return {str(k): _scrub_obj(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_obj(v, depth + 1) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _scrub(str(value))


def _task_path(task_id: str) -> Path:
    return TASK_DIR / (task_id + ".json")


def _save(task_id: str, data: dict[str, Any]) -> None:
    ensure_dirs()
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    data["id"] = task_id
    data["updated"] = _now()
    tmp = _task_path(task_id).with_name(_task_path(task_id).name + "." + str(os.getpid()) + ".tmp")
    tmp.write_text(json.dumps(_scrub_obj(data), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, _task_path(task_id))


def _load(task_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_task_path(task_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _new_task_id() -> str:
    return "claude_" + uuid.uuid4().hex[:12]


def _context_messages(context: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (context or [])[-_MAX_STORED_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in ("user", "assistant"):
            continue
        text = item.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        block = {"type": "text", "text": text[:12000]}
        if out and out[-1]["role"] == role:
            out[-1]["content"].append(block)
        else:
            out.append({"role": role, "content": [block]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _append_user(messages: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    block = {"type": "text", "text": text}
    if messages and messages[-1].get("role") == "user":
        messages[-1]["content"] = list(messages[-1].get("content") or []) + [block]
    else:
        messages.append({"role": "user", "content": [block]})
    return messages


def _result_text(result: dict[str, Any]) -> str:
    try:
        body = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        body = str(result)
    if len(body) > 14000:
        body = body[:14000] + "...[gekuerzt]"
    return body


class ClaudeAgent:
    def __init__(self, master: Any, *, client: ClaudeClient | None = None) -> None:
        self.master = master
        self.client = client or default_client()

    def _system(self) -> str:
        from nexus.common.paths import PLATFORM_NAME, ROOT
        return (SYSTEM_PROMPT + "\nPlattform: " + PLATFORM_NAME + ". Projektroot: " + str(ROOT)
                + ". Datum: " + datetime.now().strftime("%Y-%m-%d") + ".")

    def status(self) -> dict[str, Any]:
        out = self.client.status()
        out["success"] = True
        out["task_count"] = len(self.tasks(100))
        out["running"] = sorted(_RUNNING.keys())
        return out

    def capabilities(self) -> dict[str, Any]:
        from nexus.computer import desktop
        try:
            desktop_caps = desktop.capabilities()
        except Exception as exc:
            desktop_caps = {"success": False, "error": type(exc).__name__}
        return {
            "success": True,
            "provider": "claude",
            "configured": bool(self.client.api_key),
            "model": self.client.model,
            "tools": [item.get("name") for item in TOOLS],
            "desktop": desktop_caps,
            "shell": True,
            "files": True,
            "web": True,
            "youtube": True,
            "calendar": "local_nexus_store",
            "whatsapp": "phone_bridge_visible_screen",
            "spotify": "global_media_keys",
            "approval": "terminal_guard",
            "background": True,
        }

    def run(self, message: str, context: list[dict[str, Any]] | None = None, *,
            task_id: str | None = None, background: bool = False,
            session_id: str | None = None) -> dict[str, Any]:
        task_id = task_id if task_id and _TASK_ID.match(task_id) else _new_task_id()
        messages = _context_messages(context)
        _append_user(messages, str(message or ""))
        state = {"id": task_id, "status": "RUNNING", "message": str(message or ""),
                 "session_id": session_id, "background": bool(background),
                 "base_messages": messages, "assistant_content": None, "executed": [],
                 "pending": None, "round": 0, "usage": {}, "model": self.client.model,
                 "result": None, "error": None, "created": _now()}
        _save(task_id, state)
        if background:
            cancel = threading.Event()
            with _LOCK:
                _RUNNING[task_id] = cancel
            threading.Thread(target=self._thread_run, args=(task_id, state, cancel),
                             name="claude-task-" + task_id, daemon=True).start()
            return {"success": True, "status": "STARTED", "task_id": task_id,
                    "reply": "Claude arbeitet im Hintergrund. Status: claude status " + task_id,
                    "intent": "claude_background", "model": self.client.model,
                    "data": {"provider": "claude", "task_id": task_id}}
        return self._loop(task_id, state, cancel=None)

    def _thread_run(self, task_id: str, state: dict[str, Any], cancel: threading.Event) -> None:
        try:
            result = self._loop(task_id, state, cancel=cancel)
            status = str(result.get("status") or "")
            state["status"] = (status if status in ("DONE", "FAILED", "APPROVAL_REQUIRED",
                                                    "CANCELLED", "MAX_ROUNDS")
                               else ("DONE" if result.get("success") else "FAILED"))
            state["result"] = result
            state["error"] = result.get("error")
            _save(task_id, state)
        except Exception as exc:
            state["status"] = "FAILED"
            state["error"] = type(exc).__name__
            state["result"] = {"success": False, "error": type(exc).__name__,
                               "reply": "Claude-Hintergrundtask fehlgeschlagen."}
            _save(task_id, state)
        finally:
            with _LOCK:
                _RUNNING.pop(task_id, None)

    def _loop(self, task_id: str, state: dict[str, Any], *,
              cancel: threading.Event | None) -> dict[str, Any]:
        max_rounds = configured_max_tool_rounds()
        start = int(state.get("round") or 0)
        base_messages = list(state.get("base_messages") or [])
        assistant_content = state.get("assistant_content")
        executed = {str(item.get("tool_use_id")): item for item in (state.get("executed") or [])
                    if isinstance(item, dict)}
        pending = state.get("pending") if isinstance(state.get("pending"), dict) else None
        usage = dict(state.get("usage") or {})
        rounds_done = start

        for round_no in range(start, max_rounds):
            if cancel is not None and cancel.is_set():
                result = {"success": False, "status": "CANCELLED", "error": "cancelled",
                          "reply": "Claude-Auftrag abgebrochen.", "task_id": task_id,
                          "intent": "claude", "model": self.client.model}
                state.update({"status": "CANCELLED", "error": "cancelled", "result": result,
                              "round": round_no, "usage": usage})
                _save(task_id, state)
                return result
            if pending:
                content = assistant_content or []
                tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
                if not tool_uses:
                    pending = None
                    continue
                messages = list(base_messages) + [{"role": "assistant", "content": content}]
                results, pending, executed = self._execute_blocks(
                    tool_uses, executed, pending, task_id=task_id, state=state)
                if pending:
                    state.update({"base_messages": base_messages, "assistant_content": content,
                                  "executed": list(executed.values()), "pending": pending,
                                  "round": round_no, "usage": usage})
                    state["status"] = "APPROVAL_REQUIRED"
                    _save(task_id, state)
                    return self._approval_result(pending, task_id)
                messages.append({"role": "user", "content": results})
                base_messages = messages
                assistant_content = None
                state.update({"status": "RUNNING", "base_messages": base_messages,
                              "assistant_content": None, "executed": [], "pending": None,
                              "round": round_no + 1, "usage": usage})
                _save(task_id, state)
                rounds_done = round_no + 1
                continue

            resp = self.client.messages(base_messages, system=self._system(), tools=TOOLS)
            if not resp.get("success"):
                result = {"success": False, "status": "FAILED",
                          "error": resp.get("error") or "claude_failed",
                          "reply": "Claude-Fehler: " + str(resp.get("error") or "unbekannt"),
                          "task_id": task_id, "intent": "claude", "model": self.client.model,
                          "data": {"provider": "claude", "task_id": task_id,
                                   "budget": resp.get("budget")}}
                state.update({"status": "FAILED", "result": result,
                              "error": result["error"], "round": round_no, "usage": usage})
                _save(task_id, state)
                return result
            for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                        "cache_read_input_tokens"):
                usage[key] = int(usage.get(key) or 0) + int((resp.get("usage") or {}).get(key) or 0)
            content = resp.get("content") if isinstance(resp.get("content"), list) else []
            tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            if not tool_uses:
                text = str(resp.get("text") or "").strip()
                result = {"success": True, "status": "DONE", "reply": text or "Fertig.",
                          "intent": "claude", "model": resp.get("model") or self.client.model,
                          "task_id": task_id,
                          "data": {"provider": "claude", "task_id": task_id,
                                   "rounds": rounds_done + 1, "usage": usage,
                                   "stop_reason": resp.get("stop_reason"),
                                   "budget": resp.get("budget")}}
                state.update({"status": "DONE", "result": result, "error": None,
                              "round": rounds_done + 1, "usage": usage,
                              "base_messages": base_messages, "pending": None,
                              "assistant_content": None, "executed": []})
                _save(task_id, state)
                return result

            messages = list(base_messages) + [{"role": "assistant", "content": content}]
            results, pending, executed = self._execute_blocks(
                tool_uses, {}, None, task_id=task_id, state=state)
            if pending:
                state.update({"base_messages": base_messages, "assistant_content": content,
                              "executed": list(executed.values()), "pending": pending,
                              "round": round_no, "usage": usage})
                state["status"] = "APPROVAL_REQUIRED"
                _save(task_id, state)
                return self._approval_result(pending, task_id)
            messages.append({"role": "user", "content": results})
            base_messages = messages
            rounds_done = round_no + 1
            state.update({"status": "RUNNING", "base_messages": base_messages,
                          "assistant_content": None, "executed": [], "pending": None,
                          "round": rounds_done, "usage": usage})
            _save(task_id, state)

        result = {"success": False, "status": "MAX_ROUNDS", "error": "claude_max_tool_rounds",
                  "reply": "Claude hat das Rundelimit erreicht. Zwischenstand gespeichert.",
                  "task_id": task_id, "intent": "claude", "model": self.client.model,
                  "data": {"provider": "claude", "task_id": task_id, "rounds": rounds_done,
                           "usage": usage}}
        state.update({"status": "MAX_ROUNDS", "result": result, "error": result["error"],
                      "round": rounds_done, "usage": usage})
        _save(task_id, state)
        return result

    def _execute_blocks(self, tool_uses: list[dict[str, Any]], executed: dict[str, Any],
                        pending: dict[str, Any] | None, *, task_id: str,
                        state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None,
                                                        dict[str, Any]]:
        results: list[dict[str, Any]] = []
        executed = dict(executed or {})
        pending = dict(pending) if pending else None
        for index, block in enumerate(tool_uses):
            tool_id = str(block.get("id") or "")
            name = str(block.get("name") or "")
            args = block.get("input") if isinstance(block.get("input"), dict) else {}
            if tool_id in executed:
                results.append(executed[tool_id]["result"])
                continue
            if pending and pending.get("tool_use_id") == tool_id:
                args = dict(args)
                if pending.get("approval_id") and not args.get("approval_id"):
                    args["approval_id"] = pending["approval_id"]
                pending = None
            result = run_one({"name": name, "args": args}, self.master, agent=AGENT)
            if result.get("status") == "APPROVAL_REQUIRED":
                pending = {"index": index, "tool_use_id": tool_id, "name": name,
                           "args": args, "approval_id": result.get("approval_id"),
                           "result": result}
                break
            item = {"tool_use_id": tool_id,
                    "result": {"type": "tool_result", "tool_use_id": tool_id,
                               "content": _result_text(result),
                               "is_error": not bool(result.get("success"))}}
            executed[tool_id] = item
            results.append(item["result"])
        return results, pending, executed

    @staticmethod
    def _approval_result(pending: dict[str, Any], task_id: str) -> dict[str, Any]:
        tool = pending.get("name") or "tool"
        aid = pending.get("approval_id")
        reply = ("Claude braucht eine lokale Freigabe fuer " + tool + ". "
                 "approval_id: " + str(aid) + ". Nach Freigabe im Terminal-UI: "
                 "claude resume " + task_id + ".")
        return {"success": False, "status": "APPROVAL_REQUIRED",
                "error": "claude_approval_required", "approval_id": aid,
                "pending_tool": {"name": tool, "args": pending.get("args")},
                "task_id": task_id, "intent": "claude", "reply": reply,
                "data": {"provider": "claude", "task_id": task_id,
                         "approval_id": aid, "pending_tool": tool}}

    def resume(self, task_id: str, approval_id: str | None = None) -> dict[str, Any]:
        if not _TASK_ID.match(task_id or ""):
            return {"success": False, "error": "claude_invalid_task_id",
                    "reply": "Ungueltige Claude-Task-ID."}
        state = _load(task_id)
        if not state:
            return {"success": False, "error": "claude_task_not_found",
                    "reply": "Claude-Task nicht gefunden."}
        if state.get("status") == "DONE" and isinstance(state.get("result"), dict):
            return state["result"]
        pending = state.get("pending") if isinstance(state.get("pending"), dict) else None
        if approval_id and pending:
            pending = dict(pending)
            pending["approval_id"] = str(approval_id).strip()
            state["pending"] = pending
        state["status"] = "RUNNING"
        _save(task_id, state)
        return self._loop(task_id, state, cancel=None)

    def cancel(self, task_id: str) -> dict[str, Any]:
        with _LOCK:
            event = _RUNNING.get(task_id)
            if event:
                event.set()
        state = _load(task_id)
        if state:
            state["status"] = "CANCELLED"
            state["error"] = "cancelled"
            _save(task_id, state)
        return {"success": bool(state or event), "task_id": task_id,
                "status": "CANCELLED" if state else "NOT_FOUND"}

    def tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        ensure_dirs()
        TASK_DIR.mkdir(parents=True, exist_ok=True)
        items: list[dict[str, Any]] = []
        for path in TASK_DIR.glob("claude_*.json"):
            data = _load(path.stem)
            if not data:
                continue
            items.append({"id": data.get("id"), "status": data.get("status"),
                          "message": str(data.get("message") or "")[:200],
                          "updated": data.get("updated"), "created": data.get("created"),
                          "approval_id": (data.get("pending") or {}).get("approval_id")
                          if isinstance(data.get("pending"), dict) else None,
                          "error": data.get("error")})
        items.sort(key=lambda x: str(x.get("updated") or ""), reverse=True)
        return items[:max(1, min(int(limit or 20), 100))]

    def get(self, task_id: str) -> dict[str, Any]:
        state = _load(task_id)
        if not state:
            return {"success": False, "error": "claude_task_not_found"}
        return {"success": True, "task": {
            "id": state.get("id"), "status": state.get("status"),
            "message": state.get("message"), "created": state.get("created"),
            "updated": state.get("updated"), "error": state.get("error"),
            "pending": state.get("pending"), "result": state.get("result"),
            "usage": state.get("usage"),
        }}
