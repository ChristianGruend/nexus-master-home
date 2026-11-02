from __future__ import annotations
from pathlib import Path

AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v"}

def scan(root: Path) -> list:
    items = []
    if not root.exists():
        return items
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        kind = "audio" if ext in AUDIO_EXT else ("video" if ext in VIDEO_EXT else None)
        if kind:
            items.append({"name": p.name, "path": p.relative_to(root).as_posix(), "kind": kind, "size": p.stat().st_size})
    return items
