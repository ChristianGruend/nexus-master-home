"""Anthropic Claude Messages API client (optional, env-gated).

No key is ever written to logs, state or error strings. The client is
standard-library only so it works on Windows and Linux without new packages.
Budget accounting is persisted under workspace/state/claude_budget.json.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from nexus.common.paths import WORKSPACE, ensure_dirs

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-4-5"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
MAX_TOKENS_LIMIT = 32000
DEFAULT_TIMEOUT = 120.0
DEFAULT_DAILY_BUDGET = 500000

_KEY_RE = re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}\b")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int, *, low: int = 1, high: int = 10**9) -> int:
    try:
        value = int(float(_env(name) or default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def _env_float(name: str, default: float, *, low: float = 1.0, high: float = 900.0) -> float:
    try:
        value = float(_env(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def mask_secret(text: Any) -> str:
    """Remove API-key shaped tokens from any string that may leave the process."""
    return _KEY_RE.sub("sk-ant-***", str(text or ""))


def claude_available() -> bool:
    return bool(_env("ANTHROPIC_API_KEY"))


def configured_model() -> str:
    return _env("NEXUS_CLAUDE_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL


def configured_base_url() -> str:
    return (_env("NEXUS_CLAUDE_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")


def configured_max_tokens() -> int:
    return _env_int("NEXUS_CLAUDE_MAX_TOKENS", DEFAULT_MAX_TOKENS, low=1, high=MAX_TOKENS_LIMIT)


def configured_max_tool_rounds() -> int:
    return _env_int("NEXUS_CLAUDE_MAX_TOOL_ROUNDS", 12, low=1, high=60)


def configured_timeout() -> float:
    return _env_float("NEXUS_CLAUDE_TIMEOUT", DEFAULT_TIMEOUT, low=5.0, high=900.0)


def daily_token_budget() -> int:
    """0 disables the daily cap (explicit opt-out)."""
    return _env_int("NEXUS_CLAUDE_DAILY_TOKEN_BUDGET", DEFAULT_DAILY_BUDGET, low=0, high=10**12)


class ClaudeBudget:
    """Small atomic daily token counter. Thread-safe inside one process."""

    def __init__(self, path: Path | None = None) -> None:
        ensure_dirs()
        self.path = Path(path or (WORKSPACE / "state" / "claude_budget.json"))
        self._lock = threading.RLock()

    def _today(self) -> str:
        return date.today().isoformat()

    def _empty(self) -> dict[str, Any]:
        return {"date": self._today(), "input_tokens": 0, "output_tokens": 0,
                "requests": 0, "updated": None}

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if data.get("date") != self._today():
                    return self._empty()
                for key in ("input_tokens", "output_tokens", "requests"):
                    data[key] = max(0, int(data.get(key) or 0))
                return data
        except Exception:
            pass
        return self._empty()

    def _save(self, data: dict[str, Any]) -> None:
        data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + "." + str(os.getpid()) + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = self._load()
        used = int(data.get("input_tokens") or 0) + int(data.get("output_tokens") or 0)
        limit = daily_token_budget()
        return {**data, "used_tokens": used, "limit": limit,
                "remaining": max(0, limit - used) if limit else None,
                "exceeded": bool(limit and used >= limit), "path": str(self.path)}

    def check(self, estimated_tokens: int = 0) -> dict[str, Any]:
        snap = self.snapshot()
        limit = snap["limit"]
        if limit and snap["used_tokens"] + max(0, int(estimated_tokens)) > limit:
            return {"success": False, "error": "claude_budget_exceeded",
                    "message": "Tagesbudget fuer Claude ist erschoepft.",
                    "budget": snap}
        return {"success": True, "budget": snap}

    def record(self, usage: dict[str, Any] | None) -> dict[str, Any]:
        usage = usage or {}
        with self._lock:
            data = self._load()
            data["input_tokens"] += max(0, int(usage.get("input_tokens") or 0))
            data["output_tokens"] += max(0, int(usage.get("output_tokens") or 0))
            data["requests"] += 1
            self._save(data)
        return self.snapshot()


class ClaudeClient:
    """Minimal Messages API wrapper with budget, timeout and safe errors."""

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 base_url: str | None = None, timeout: float | None = None,
                 budget: ClaudeBudget | None = None) -> None:
        self.api_key = (api_key if api_key is not None else _env("ANTHROPIC_API_KEY")).strip()
        self.model = (model or configured_model()).strip()
        self.base_url = (base_url or configured_base_url()).rstrip("/")
        self.timeout = float(timeout or configured_timeout())
        self.budget = budget or ClaudeBudget()

    def status(self) -> dict[str, Any]:
        return {
            "provider": "claude",
            "configured": bool(self.api_key),
            "reachable": None,
            "model": self.model,
            "base_url": self.base_url,
            "optional": True,
            "status": "OPTIONAL",
            "blocks_health": False,
            "budget": self.budget.snapshot(),
        }

    def messages(self, messages: list[dict[str, Any]], *, system: str | None = None,
                 tools: list[dict[str, Any]] | None = None, model: str | None = None,
                 max_tokens: int | None = None, temperature: float | None = None,
                 timeout: float | None = None) -> dict[str, Any]:
        if not self.api_key:
            return {"success": False, "error": "claude_not_configured",
                    "message": "ANTHROPIC_API_KEY ist nicht gesetzt.", "model": self.model}

        use_model = (model or self.model).strip()
        use_max = int(max_tokens or configured_max_tokens())
        payload: dict[str, Any] = {"model": use_model, "max_tokens": use_max,
                                   "messages": messages or []}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = max(0.0, min(float(temperature), 1.0))

        estimate = (len(json.dumps(payload, ensure_ascii=False)) // 4) + use_max
        gate = self.budget.check(estimate)
        if not gate.get("success"):
            return {**gate, "model": use_model}

        req = urllib.request.Request(
            self.base_url + "/v1/messages",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
                "accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=float(timeout or self.timeout)) as resp:
                raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw)
        except urllib.error.HTTPError as exc:
            return {"success": False, "error": self._http_error(exc), "model": use_model,
                    "status_code": int(getattr(exc, "code", 0) or 0)}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"success": False, "error": "claude_transport_" + type(exc).__name__,
                    "message": mask_secret(exc)[:300], "model": use_model}
        except ValueError as exc:
            return {"success": False, "error": "claude_invalid_json",
                    "message": mask_secret(exc)[:300], "model": use_model}

        if not isinstance(data, dict):
            return {"success": False, "error": "claude_invalid_response", "model": use_model}
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        budget = self.budget.record(usage)
        content = data.get("content") if isinstance(data.get("content"), list) else []
        return {
            "success": True,
            "model": data.get("model") or use_model,
            "content": content,
            "text": content_text(content),
            "stop_reason": data.get("stop_reason"),
            "usage": usage,
            "budget": budget,
            "request_id": data.get("id"),
        }

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError) -> str:
        code = int(getattr(exc, "code", 0) or 0)
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        kind, message = "http_error", ""
        try:
            data = json.loads(body)
            err = data.get("error") if isinstance(data, dict) else None
            if isinstance(err, dict):
                kind = str(err.get("type") or kind)
                message = str(err.get("message") or "")
            elif isinstance(err, str):
                message = err
        except Exception:
            message = body[:300]
        if code == 401:
            kind = "authentication_error"
        elif code == 429:
            kind = "rate_limited"
        elif code == 400 and kind == "http_error":
            kind = "bad_request"
        safe = mask_secret(message).strip().replace("\n", " ")[:300]
        return f"claude_http_{code}:{kind}" + (":" + safe if safe else "")


def content_text(content: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts).strip()


_BUDGET: ClaudeBudget | None = None
_BUDGET_LOCK = threading.Lock()


def default_budget() -> ClaudeBudget:
    global _BUDGET
    with _BUDGET_LOCK:
        if _BUDGET is None:
            _BUDGET = ClaudeBudget()
        return _BUDGET


def default_client() -> ClaudeClient:
    return ClaudeClient(budget=default_budget())
