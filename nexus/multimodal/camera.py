"""Camera modality — UNKNOWN (no capture pipeline in tree)."""
from __future__ import annotations
from typing import Any


def status(master: Any = None) -> dict:
    from nexus.multimodal.registry import _camera_status
    return _camera_status(master)


def capture(_master: Any = None) -> dict:
    return {
        "success": False,
        "ok": False,
        "status": "UNKNOWN",
        "error": "camera_not_implemented",
        "reply": "Kamera-Capture ist nicht verdrahtet (UNKNOWN — kein Fake-DONE).",
    }
