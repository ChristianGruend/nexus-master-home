"""NEXUS Studio Video (ffmpeg) (Seed 2026-10-29, ST06/ST14/ST15/ST16). Nur lokal, nur G:, 0 EUR.
Leitbild: Original bleibt unveraendert (neue Version), ehrliche Fehler, KI-Inhalte kennzeichnen."""
from __future__ import annotations

import subprocess
from pathlib import Path

FFMPEG = Path(r"G:\eddyzeug\Projekte\hermes\tools\ffmpeg-9.0.1-win32-x64\bin\ffmpeg.exe")
FFPROBE = FFMPEG.with_name("ffprobe.exe")
ANIM_LABEL = "Animation, keine KI-Generierung"


class VideoError(Exception):
    pass


def _run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    if not FFMPEG.is_file():
        raise VideoError("FFMPEG_MISSING")
    return subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", *args],
                          capture_output=True, text=True, timeout=timeout)


def probe(path) -> dict:
    """Dauer (s), Breite, Hoehe und comment-Metadatum per ffprobe."""
    import json
    p = subprocess.run([str(FFPROBE), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, timeout=60)
    d = json.loads(p.stdout or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"duration": float(d.get("format", {}).get("duration", 0) or 0), "width": v.get("width"),
            "height": v.get("height"), "comment": (d.get("format", {}).get("tags") or {}).get("comment", "")}


def ken_burns(image, out, seconds: float = 5.0, fps: int = 25) -> dict:
    """ST06: Standbild -> MP4 mit langsamem Zoom (Ken-Burns), Metadatum comment=ANIM_LABEL."""
    raise VideoError("NOT_IMPLEMENTED:ken_burns")


def trim(src, out, start: float, end: float) -> dict:
    """ST14: Ausschnitt start..end als neue Datei; Original unveraendert."""
    raise VideoError("NOT_IMPLEMENTED:trim")


def vertical(src, out) -> dict:
    """ST14: 9:16 -> 1080x1920 (skalieren + Rand/Zuschnitt)."""
    raise VideoError("NOT_IMPLEMENTED:vertical")


def effects(src, out, text: str = "", gif: bool = False) -> dict:
    """ST15: Text-Overlay (drawtext) bzw. GIF-Export."""
    raise VideoError("NOT_IMPLEMENTED:effects")


def slideshow(images: list, out, per_image: float = 2.0) -> dict:
    """ST16: Diashow, Dauer = n * per_image."""
    raise VideoError("NOT_IMPLEMENTED:slideshow")


def still(src, out, at: float = 0.0) -> dict:
    """ST16: Standbild PNG aus Video."""
    raise VideoError("NOT_IMPLEMENTED:still")
