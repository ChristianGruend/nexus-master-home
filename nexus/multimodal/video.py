"""Video modality — library/playback PARTIAL; no capture."""
from __future__ import annotations
from typing import Any


def status(master: Any = None) -> dict:
    from nexus.multimodal.registry import _video_status
    return _video_status(master)


def list_library() -> dict:
    from nexus.media.video_library import VideoLibrary
    return VideoLibrary().list()
