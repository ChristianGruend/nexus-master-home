"""Model routing: local-first (Ollama), optional cloud only if key + explicit."""
from __future__ import annotations

import os
from typing import Any


def route_model(prefer: str | None = None, allow_cloud: bool = False) -> dict[str, Any]:
    """Return routing decision. Cost rule LOCAL -> FREE -> NOT."""
    prefer = (prefer or "").strip().lower()
    has_openai = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    if prefer in ("openai", "cloud") and allow_cloud and has_openai:
        return {"provider": "openai", "model": "gpt-4o-mini", "cost": "PAID", "used": "explicit"}
    # default local
    from nexus.common.config import Config
    return {
        "provider": "ollama",
        "model": Config.PREFERRED_MODEL,
        "cost": "LOCAL",
        "openai_key_present": has_openai,
        "openai_required": False,
        "rule": "LOCAL->FREE->NOT",
    }
