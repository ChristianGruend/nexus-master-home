"""Local HTTP handlers for /api/multimodal* (NOT on Funnel/Gateway allowlist)."""
from __future__ import annotations
from typing import Any


def is_multimodal_get(path: str) -> bool:
    p = (path or "").split("?", 1)[0]
    return p == "/api/multimodal" or p.startswith("/api/multimodal/")


def is_multimodal_post(path: str) -> bool:
    return is_multimodal_get(path)


def handle_get(handler: Any, path: str, qs: dict | None = None, master: Any = None) -> None:
    qs = qs or {}
    p = (path or "").split("?", 1)[0]
    from nexus.multimodal.registry import multimodal_status, modality_matrix

    if p == "/api/multimodal" or p == "/api/multimodal/status":
        return handler._json(200, multimodal_status(master))

    if p == "/api/multimodal/matrix":
        return handler._json(200, {
            "success": True,
            "ok": True,
            "matrix": modality_matrix(master),
            "local_only": True,
            "funnel_exposed": False,
        })

    if p.startswith("/api/multimodal/"):
        name = p[len("/api/multimodal/"):].strip("/").split("/")[0]
        rows = {r["modality"]: r for r in modality_matrix(master)}
        # aliases
        aliases = {
            "screen": "screen_share",
            "screenshare": "screen_share",
            "screen_share": "screen_share",
            "vision": "image",
            "img": "image",
        }
        key = aliases.get(name, name)
        row = rows.get(key)
        if not row:
            return handler._json(404, {"success": False, "error": "unknown_modality", "modality": name})
        return handler._json(200, {"success": True, "ok": True, "modality": row})

    return handler._json(404, {"success": False, "error": "not_found", "path": path})


def handle_post(handler: Any, path: str, body: dict | None = None, master: Any = None) -> None:
    """POST limited: camera/share explicitly return UNKNOWN (no fake success)."""
    body = body or {}
    p = (path or "").split("?", 1)[0]

    if p in ("/api/multimodal/camera/capture", "/api/multimodal/camera"):
        from nexus.multimodal.camera import capture
        return handler._json(200, capture(master))

    if p in ("/api/multimodal/screen/share", "/api/multimodal/screen_share", "/api/multimodal/screen/start"):
        from nexus.multimodal.screen import start_share
        return handler._json(200, start_share(master))

    return handler._json(404, {
        "success": False,
        "error": "not_found",
        "path": path,
        "hint": "Use existing /api/vision, /api/voice/*, /api/chat for working modalities.",
    })
