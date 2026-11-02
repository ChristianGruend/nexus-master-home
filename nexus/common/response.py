"""Minimal Core API envelope helpers (ok/status/error/details). No large refactor."""
from __future__ import annotations

from typing import Any


def envelope(
    success: bool,
    *,
    error: str | None = None,
    details: dict | None = None,
    status: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Unified shape: success + ok + status (+ optional error/details) + extras."""
    out: dict[str, Any] = {
        "success": bool(success),
        "ok": bool(success),
        "status": status if status is not None else ("ok" if success else "error"),
    }
    if error is not None:
        out["error"] = error
    if details is not None:
        out["details"] = details
    out.update(extra)
    return out


def attach_ok(payload: dict[str, Any]) -> dict[str, Any]:
    """Add ok/status to an existing success/error dict without dropping fields."""
    if not isinstance(payload, dict):
        return envelope(False, error="invalid_payload", details={"got": type(payload).__name__})
    out = dict(payload)
    success = bool(out.get("success", False))
    out["ok"] = success if "ok" not in out else bool(out.get("ok"))
    if "status" not in out:
        out["status"] = "ok" if success else "error"
    return out
