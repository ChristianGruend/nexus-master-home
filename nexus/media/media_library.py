from __future__ import annotations
from nexus.common.paths import MEDIA_DIR, ensure_dirs
from .playlist import scan

class MediaLibrary:
    def __init__(self) -> None:
        ensure_dirs()
        self.root = MEDIA_DIR

    def list(self) -> dict:
        items = scan(self.root)
        return {"success": True, "items": items, "root": str(self.root)}
