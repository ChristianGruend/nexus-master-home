"""Screen modality.
- screen_analyze (upload frame + llava): existing /api/screen/analyze — NOT live share
- screen_share (live): UNKNOWN — do not claim DONE
"""
from __future__ import annotations
from typing import Any


def status(master: Any = None) -> dict:
    from nexus.multimodal.registry import _screen_share_status
    return _screen_share_status(master)


def analyze_frame(master: Any, image_bytes: bytes, prompt: str | None = None) -> dict:
    """Same as image.analyze — for uploaded screen frames only."""
    from nexus.multimodal.image import analyze
    return analyze(master, image_bytes, prompt)


def start_share(_master: Any = None) -> dict:
    return {
        "success": False,
        "ok": False,
        "status": "UNKNOWN",
        "error": "screen_share_not_implemented",
        "reply": "Live Screen Share ist UNKNOWN (kein WebRTC/Capture). Frame-Analyze via /api/screen/analyze existiert separat.",
    }
