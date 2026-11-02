"""OpenAI cloud orchestrator for NEXUS (Responses API + local Agent Bridge).

Loads OPENAI_API_KEY from AppData or process env (never logs the key).
Calls local Bridge at 127.0.0.1:8299 for grounded actions — never invents PASS.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from nexus.common.paths import APEX, ROOT, WORKSPACE

OPENAI_URL = "https://api.openai.com/v1/responses"
DEFAULT_BRIDGE = "http://127.0.0.1:8299"
SYSTEM_PROMPT_PATH = APEX / "openai_orchestrator_system.txt"

# Prefer documented AppData path; never commit project .env.openai
APPDATA_OPENAI_ENV = Path(os.environ.get("LOCALAPPDATA", "")) / "NEXUS" / "openai.env"

MODEL_CANDIDATES = (
    "gpt-6-luna",
    "gpt-5",
    "gpt-4o",
)

BRIDGE_TASKS = (
    "ping",
    "system_status",
    "inspect_server",
    "read_file",
    "list_files",
    "git_status",
    "capability_check",
)


def _mask(tok: str) -> str:
    if not tok:
        return "(leer)"
    if len(tok) <= 8:
        return "..." + tok[-4:]
    return "..." + tok[-4:]


def load_openai_api_key() -> str:
    """Load OPENAI_API_KEY into process env. Prefer AppData; never print value."""
    existing = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if existing:
        return existing

    candidates = [
        APPDATA_OPENAI_ENV,
        WORKSPACE / "secrets" / "openai.env",
        ROOT / ".env.openai",  # discouraged; gitignored
    ]
    for fp in candidates:
        try:
            if not fp or not fp.is_file():
                continue
            for line in fp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.upper().startswith("OPENAI_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        os.environ["OPENAI_API_KEY"] = val
                        return val
        except Exception:
            continue
    return ""


def load_system_prompt() -> str:
    try:
        if SYSTEM_PROMPT_PATH.is_file():
            t = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
            if t:
                return t
    except Exception:
        pass
    return (
        "Du bist NEXUS Master Home Cloud-Orchestrator. "
        "Nutze lokalen Agent Bridge http://127.0.0.1:8299. "
        "Nie PASS erfinden. Antworte auf Deutsch."
    )


def _bridge_token() -> str:
    tok = (os.environ.get("NEXUS_BRIDGE_TOKEN") or "").strip()
    if tok:
        return tok
    for fp in (
        WORKSPACE / "bridge" / ".token",
        WORKSPACE / "certs" / ".bridge_token",
    ):
        try:
            if fp.is_file():
                t = fp.read_text(encoding="utf-8").strip()
                if t:
                    os.environ["NEXUS_BRIDGE_TOKEN"] = t
                    return t
        except Exception:
            pass
    return ""


def bridge_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    base: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Call local Agent Bridge. Returns {ok, status, data|error}."""
    base = (base or os.environ.get("NEXUS_BRIDGE_URL") or DEFAULT_BRIDGE).rstrip("/")
    url = base + path
    tok = _bridge_token()
    headers = {"Accept": "application/json"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                parsed = {"raw": raw[:4000]}
            return {"ok": True, "status": int(resp.status), "data": parsed, "url": url}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"raw": raw[:2000]}
        return {
            "ok": False,
            "status": int(e.code),
            "error": parsed or str(e),
            "url": url,
        }
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e), "url": url}


def gather_bridge_grounding(base: str | None = None) -> dict[str, Any]:
    """Real Bridge probes — never invent results."""
    out: dict[str, Any] = {
        "bridge_base": (base or DEFAULT_BRIDGE),
        "token_mask": _mask(_bridge_token()),
        "probes": {},
    }
    out["probes"]["health"] = bridge_request("GET", "/api/bridge/health", base=base)
    out["probes"]["capabilities"] = bridge_request("GET", "/api/capabilities", base=base)
    out["probes"]["system_status"] = bridge_request(
        "POST",
        "/api/task",
        {"task": "system_status", "params": {}, "source": "openai_orchestrator"},
        base=base,
    )
    out["probes"]["inspect_server"] = bridge_request(
        "POST",
        "/api/task",
        {"task": "inspect_server", "params": {}, "source": "openai_orchestrator"},
        base=base,
    )
    return out


def _record_openai_failure(*, status: int, error_type: str, model: str) -> dict[str, Any]:
    """Persist only non-secret diagnostic metadata; never response bodies or credentials."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": "openai",
        "http_status": int(status or 0),
        "error_type": str(error_type or "UnknownError")[:120],
        "model": str(model or "")[:120],
    }
    try:
        log = WORKSPACE / "logs" / "openai_diagnostics.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return event


def _openai_post(payload: dict[str, Any], api_key: str, timeout: float = 120.0) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": int(resp.status), "data": json.loads(raw)}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            parsed = json.loads(raw) if raw else {"message": str(e)}
        except Exception:
            parsed = {"message": raw[:4000] or str(e)}
        return {"ok": False, "status": int(e.code), "error": parsed, "error_type": type(e).__name__}
    except Exception as e:
        return {"ok": False, "status": 0, "error": {"message": str(e)}, "error_type": type(e).__name__}


def diagnose_openai_once(model: str, message: str = "Reply only: OK") -> dict[str, Any]:
    """Explicit one-shot OpenAI diagnostic: one model, one HTTP request, no tools/fallback."""
    chosen_model = (model or "").strip()
    if not chosen_model:
        return {"success": False, "cloud_success": False, "error": "model_required", "request_count": 0}
    msg = (message or "Reply only: OK").strip()[:400]
    api_key = load_openai_api_key()
    if not api_key:
        return {
            "success": False, "cloud_success": False, "error": "openai_api_key_missing",
            "model": chosen_model, "request_count": 0, "mode": "cloud/openai/diagnostic_once",
            "bridge_fallback": False,
        }
    payload = {
        "model": chosen_model,
        "input": msg,
        "max_output_tokens": 16,
    }
    resp = _openai_post(payload, api_key)
    status = int(resp.get("status") or 0)
    if not resp.get("ok"):
        diagnostic = _record_openai_failure(
            status=status,
            error_type=str(resp.get("error_type") or ("HTTPError" if status else "NetworkError")),
            model=chosen_model,
        )
        return {
            "success": False, "cloud_success": False, "error": "openai_diagnostic_failed",
            "model": chosen_model, "http_status": status,
            "error_type": diagnostic["error_type"], "timestamp": diagnostic["timestamp"],
            "request_count": 1, "mode": "cloud/openai/diagnostic_once", "bridge_fallback": False,
        }
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    return {
        "success": True, "cloud_success": True, "model": chosen_model, "http_status": status,
        "error_type": None, "request_count": 1, "mode": "cloud/openai/diagnostic_once",
        "bridge_fallback": False, "reply": _extract_output_text(data)[:200],
    }


def _extract_output_text(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    t = data.get("output_text")
    if isinstance(t, str) and t.strip():
        return t.strip()
    parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                    txt = c.get("text") or ""
                    if txt:
                        parts.append(str(txt))
        elif item.get("type") == "output_text" and item.get("text"):
            parts.append(str(item["text"]))
    return "\n".join(parts).strip()


def _extract_function_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in (data.get("output") or []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") in ("function_call", "custom_tool_call"):
            calls.append(item)
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "function_call":
                    calls.append(c)
    return calls


def _bridge_function_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "bridge_task",
            "description": (
                "Fuehre eine registrierte NEXUS Agent-Bridge Task lokal aus "
                f"(READ_ONLY). Tasks: {', '.join(BRIDGE_TASKS)}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "enum": list(BRIDGE_TASKS)},
                    "params": {"type": "object"},
                },
                "required": ["task"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "bridge_message",
            "description": "Sende eine leichte Nachricht an POST /api/message der lokalen Bridge.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    ]


def _run_bridge_tool(name: str, arguments: Any, base: str | None) -> dict[str, Any]:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments else {}
        except Exception:
            arguments = {"_raw": arguments}
    arguments = arguments or {}
    if name == "bridge_task":
        task = str(arguments.get("task") or "").strip()
        params = arguments.get("params") if isinstance(arguments.get("params"), dict) else {}
        return bridge_request(
            "POST",
            "/api/task",
            {"task": task, "params": params, "source": "openai_orchestrator"},
            base=base,
        )
    if name == "bridge_message":
        msg = str(arguments.get("message") or "").strip()
        return bridge_request(
            "POST",
            "/api/message",
            {"message": msg, "source": "openai_orchestrator"},
            base=base,
        )
    return {"ok": False, "error": f"unknown tool {name}"}


def _build_payload(
    model: str,
    instructions: str,
    input_items: list[Any],
    *,
    use_web_search: bool,
) -> dict[str, Any]:
    tools: list[dict[str, Any]] = list(_bridge_function_tools())
    if use_web_search:
        tools.insert(0, {"type": "web_search"})
    return {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "tools": tools,
        "tool_choice": "auto",
    }


def _tool_loop(
    model: str,
    instructions: str,
    user_text: str,
    grounding: dict[str, Any],
    api_key: str,
    *,
    bridge_base: str | None,
    use_web_search: bool,
    max_rounds: int = 4,
) -> dict[str, Any]:
    grounded_blob = json.dumps(grounding, ensure_ascii=False, default=str)[:24000]
    input_items: list[Any] = [
        {
            "role": "user",
            "content": (
                f"{user_text}\n\n"
                "---\nGEMESSENE BRIDGE-GROUNDING (echt, nicht erfinden):\n"
                f"{grounded_blob}\n"
                "Nutze bei Bedarf bridge_task / bridge_message / web_search. "
                "Antworte grounded auf Deutsch."
            ),
        }
    ]
    attempts: list[dict[str, Any]] = []
    last_data: dict[str, Any] | None = None
    tool_results: list[dict[str, Any]] = []

    for round_i in range(max_rounds):
        payload = _build_payload(
            model, instructions, input_items, use_web_search=use_web_search
        )
        resp = _openai_post(payload, api_key)
        attempts.append(
            {
                "round": round_i,
                "ok": resp.get("ok"),
                "status": resp.get("status"),
                "web_search": use_web_search,
            }
        )
        if not resp.get("ok"):
            return {
                "ok": False,
                "model": model,
                "error": resp.get("error"),
                "error_type": resp.get("error_type") or ("HTTPError" if resp.get("status") else "NetworkError"),
                "status": resp.get("status"),
                "attempts": attempts,
                "tool_results": tool_results,
                "web_search": use_web_search,
            }
        data = resp["data"]
        last_data = data
        calls = _extract_function_calls(data)
        if not calls:
            text = _extract_output_text(data)
            return {
                "ok": True,
                "model": model,
                "reply": text,
                "raw": data,
                "attempts": attempts,
                "tool_results": tool_results,
                "web_search": use_web_search,
                "response_id": data.get("id"),
            }
        for item in data.get("output") or []:
            if isinstance(item, dict):
                input_items.append(item)
        for call in calls:
            name = call.get("name") or call.get("tool_name") or ""
            args = call.get("arguments") or call.get("input") or {}
            call_id = call.get("call_id") or call.get("id") or ""
            result = _run_bridge_tool(str(name), args, bridge_base)
            tool_results.append({"name": name, "arguments": args, "result": result})
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result, ensure_ascii=False, default=str)[:20000],
                }
            )

    text = _extract_output_text(last_data or {})
    return {
        "ok": bool(text),
        "model": model,
        "reply": text or "BLOCKED: tool loop ohne finale Antwort",
        "raw": last_data,
        "attempts": attempts,
        "tool_results": tool_results,
        "web_search": use_web_search,
        "error": None if text else {"message": "max_rounds_exceeded"},
    }


def bridge_grounded_recommendation(grounding: dict[str, Any], user_message: str = "") -> str:
    """Deutscher naechster Schritt nur aus echten Bridge-Probes (kein Fake-PASS)."""
    probes = (grounding or {}).get("probes") or {}
    health = probes.get("health") or {}
    sys_t = probes.get("system_status") or {}
    insp = probes.get("inspect_server") or {}
    lines = [
        "OpenAI-Cloud war nicht verfuegbar oder Auth fehlgeschlagen — Empfehlung nur aus lokalem Agent Bridge.",
    ]
    if health.get("ok"):
        data = health.get("data") or {}
        lines.append(
            f"Bridge Health: OK marker={data.get('marker')} protocol={data.get('protocol')} v={data.get('version')}."
        )
    else:
        lines.append(f"Bridge Health: FEHLER status={health.get('status')} error={health.get('error')}")
    if sys_t.get("ok"):
        task = (sys_t.get("data") or {}).get("task") or {}
        lines.append(f"system_status Task {task.get('id')}: {task.get('status')}.")
    else:
        lines.append(f"system_status: nicht ok status={sys_t.get('status')}")
    if insp.get("ok"):
        task = (insp.get("data") or {}).get("task") or {}
        lines.append(f"inspect_server Task {task.get('id')}: {task.get('status')}.")
    # Concrete next step
    lines.append(
        "Naechster sinnvoller Schritt: Gueltigen OPENAI_API_KEY (Format sk-...) in "
        r"%LOCALAPPDATA%\NEXUS\openai.env setzen und POST /api/orchestrate erneut aufrufen; "
        "bis dahin lokalen Self-Improve/Bridge READ_ONLY nutzen (capability_check + git_status) "
        "und keinen Cloud-PASS erfinden."
    )
    return "\n".join(lines)



def orchestrate(
    message: str,
    *,
    bridge_base: str | None = None,
    models: tuple[str, ...] | None = None,
    skip_grounding: bool = False,
) -> dict[str, Any]:
    """Run OpenAI Responses orchestrator with Bridge grounding.

    Tries models in order (gpt-6-luna → gpt-5 → gpt-4o). Reports real API errors.
    """
    msg = (message or "").strip()
    if not msg:
        return {"success": False, "error": "message_required", "reply": ""}

    api_key = load_openai_api_key()
    if not api_key:
        return {
            "success": False,
            "error": "openai_api_key_missing",
            "reply": (
                "BLOCKED: OPENAI_API_KEY fehlt. Setze Env oder "
                r"C:\Users\gustl\AppData\Local\NEXUS\openai.env"
            ),
            "key_source_hint": str(APPDATA_OPENAI_ENV),
        }

    instructions = load_system_prompt()
    if skip_grounding:
        grounding: dict[str, Any] = {"skipped": True}
    else:
        grounding = gather_bridge_grounding(bridge_base)

    model_list = models or MODEL_CANDIDATES
    model_errors: list[dict[str, Any]] = []
    last_failure: dict[str, Any] | None = None

    for model in model_list:
        for use_ws in (True, False):
            result = _tool_loop(
                model,
                instructions,
                msg,
                grounding,
                api_key,
                bridge_base=bridge_base,
                use_web_search=use_ws,
            )
            if result.get("ok"):
                return {
                    "success": True,
                    "mode": "cloud/openai",
                    "model": result["model"],
                    "reply": result.get("reply") or "",
                    "web_search": result.get("web_search"),
                    "response_id": result.get("response_id"),
                    "bridge_grounding": {
                        "token_mask": grounding.get("token_mask"),
                        "health_ok": (grounding.get("probes") or {}).get("health", {}).get("ok"),
                        "system_status_ok": (grounding.get("probes") or {})
                        .get("system_status", {})
                        .get("ok"),
                        "probes": grounding.get("probes"),
                    },
                    "tool_results": result.get("tool_results") or [],
                    "model_errors": model_errors,
                    "source": "openai_orchestrator",
                }
            err = result.get("error") or {}
            err_s = json.dumps(err, ensure_ascii=False) if not isinstance(err, str) else err
            status = result.get("status") or 0
            diagnostic = _record_openai_failure(
                status=status,
                error_type=str(result.get("error_type") or ("HTTPError" if status else "NetworkError")),
                model=model,
            )
            model_errors.append(
                {
                    "model": model,
                    "status": status,
                    "http_status": status,
                    "error_type": diagnostic["error_type"],
                    "timestamp": diagnostic["timestamp"],
                    "web_search": use_ws,
                }
            )
            last_failure = result
            if use_ws and status in (400, 404, 422):
                continue
            if status in (404, 400) and any(
                k in err_s.lower()
                for k in ("model", "does not exist", "invalid", "not found", "unknown")
            ):
                break
            if status in (401, 403):
                return {
                    "success": False,
                    "error": "openai_auth_failed",
                    "reply": "",
                    "cloud_success": False,
                    "diagnostic_recommendation": bridge_grounded_recommendation(grounding, msg),
                    "openai_blocked": "BLOCKED: OpenAI Auth fehlgeschlagen.",
                    "model_errors": model_errors,
                    "bridge_grounding": {
                        "token_mask": grounding.get("token_mask"),
                        "health_ok": (grounding.get("probes") or {}).get("health", {}).get("ok"),
                        "system_status_ok": (grounding.get("probes") or {}).get("system_status", {}).get("ok"),
                        "probes": grounding.get("probes"),
                    },
                    "bridge_fallback": False,
                    "mode": "cloud/openai",
                    "source": "openai_orchestrator",
                }
            if not use_ws:
                break

    return {
        "success": False,
        "cloud_success": False,
        "mode": "cloud/openai",
        "bridge_fallback": False,
        "error": "all_models_failed",
        "reply": "",
        "diagnostic_recommendation": bridge_grounded_recommendation(grounding, msg),
        "model_errors": model_errors,
        "last_failure": {
            "status": (last_failure or {}).get("status"),
            "error": (last_failure or {}).get("error"),
        },
        "bridge_grounding": {
            "token_mask": grounding.get("token_mask"),
            "probes": grounding.get("probes"),
        },
        "source": "openai_orchestrator",
    }


def bridge_token_howto() -> dict[str, str]:
    return {
        "env": "NEXUS_BRIDGE_TOKEN",
        "file_primary": str(WORKSPACE / "bridge" / ".token"),
        "file_alt": str(WORKSPACE / "certs" / ".bridge_token"),
        "header": "Authorization: Bearer <token>",
        "load_on_start": "start_nexus.ps1 liest Datei in Env wenn Env leer",
        "generate": "Fehlt Token: bridge.auth.ensure_bridge_token() schreibt workspace/bridge/.token",
        "openai_key_preferred": str(APPDATA_OPENAI_ENV),
        "openai_key_env": "OPENAI_API_KEY",
        "do_not_commit": ".env.openai / workspace/bridge/.token / openai.env",
    }
