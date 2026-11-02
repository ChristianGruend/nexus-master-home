"""Optional OpenAI client — only if OPENAI_API_KEY in env. Never required."""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


def openai_available() -> bool:
    return bool((os.environ.get("OPENAI_API_KEY") or "").strip())


def chat_completion(messages: list[dict[str, str]], model: str = "gpt-4o-mini") -> dict[str, Any]:
    """Best-effort OpenAI Chat Completions. Returns structured error if no key."""
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return {
            "success": False,
            "error": "OPENAI_API_KEY not set",
            "openai_required": False,
            "hint": "Local Ollama is preferred (LOCAL->FREE->NOT)",
        }
    body = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"success": True, "data": data, "paid": True}
    except Exception as e:
        return {"success": False, "error": type(e).__name__, "message": str(e)[:300]}
