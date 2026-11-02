"""Live-Stream-State — nur autorisierte http/https-URLs. Kein DRM-Bypass.

Der Browser spielt die URL; der Server hält nur Metadaten und optional
letzten KI-Kommentar. Nutzer muss berechtigt sein, die URL abzurufen.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any
from urllib.parse import urlparse


def normalize_url(url: str) -> str | None:
    u = (url or "").strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return None


def detect_embed(url: str) -> dict[str, Any] | None:
    """Best-effort YouTube/Twitch embed — spielt in NEXUS (iframe), kein DRM-Bypass."""
    try:
        p = urlparse(url)
    except Exception:
        return None
    host = (p.netloc or "").lower()
    path = p.path or ""
    if "youtube.com" in host or "youtu.be" in host or "youtube-nocookie.com" in host:
        vid = None
        if "youtu.be" in host:
            vid = path.strip("/").split("/")[0] or None
        else:
            m = re.search(r"[?&]v=([^&]+)", url)
            if m:
                vid = m.group(1)
            if not vid:
                m = re.search(r"/embed/([^/?]+)", path)
                if m:
                    vid = m.group(1)
            if not vid:
                m = re.search(r"/shorts/([^/?]+)", path)
                if m:
                    vid = m.group(1)
            if not vid:
                m = re.search(r"/live/([^/?]+)", path)
                if m:
                    vid = m.group(1)
        if vid:
            # nocookie + autoplay mute-friendly params; user can unmute in player
            emb = (
                f"https://www.youtube-nocookie.com/embed/{vid}"
                f"?autoplay=1&rel=0&modestbranding=1&playsinline=1"
            )
            return {
                "kind": "youtube",
                "video_id": vid,
                "embed": emb,
                "note": "YouTube läuft direkt in NEXUS (offizieller Embed-Player).",
            }
    if "twitch.tv" in host:
        parts = [x for x in path.strip("/").split("/") if x]
        channel = parts[0] if parts else None
        if channel and channel not in ("videos", "directory", "downloads"):
            return {
                "kind": "twitch",
                "embed": (
                    f"https://player.twitch.tv/?channel={channel}"
                    f"&parent=localhost&parent=127.0.0.1&autoplay=true"
                ),
                "note": "Twitch läuft in NEXUS (Embed). Bei Problemen Direkt-URL (mp4/m3u8) nutzen.",
            }
    return None


class StreamManager:
    """In-memory list of followed streams {id, url, title, status, last_comment}."""

    def __init__(self) -> None:
        self.streams: list[dict] = []
        self.active_id: str | None = None

    def list(self) -> dict:
        return {
            "success": True,
            "active": self.active_id,
            "streams": list(self.streams),
            "notice": (
                "Nur http/https-URLs. Kein DRM-Bypass. "
                "Du musst zur Nutzung der Quelle berechtigt sein. "
                "Viele Twitch/YT-Streams brauchen Embed statt direktem <video>."
            ),
        }

    def add(self, url: str, title: str | None = None) -> dict:
        norm = normalize_url(url)
        if not norm:
            return {
                "success": False,
                "error": "bad_url",
                "reply": "Nur http:// oder https:// URLs erlaubt.",
            }
        sid = "STR-" + uuid.uuid4().hex[:10]
        embed = detect_embed(norm)
        entry = {
            "id": sid,
            "url": norm,
            "title": (title or "").strip() or norm[:64],
            "status": "idle",
            "last_comment": None,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "embed": embed,
        }
        self.streams.append(entry)
        self.active_id = sid
        return {
            "success": True,
            "stream": entry,
            "reply": f"Stream hinzugefügt: {entry['title']}",
            "notice": (embed["note"] if embed else "Direkte Media-URL (mp4/webm/hls) empfohlen für lokalen Player."),
        }

    def remove(self, stream_id: str) -> dict:
        before = len(self.streams)
        self.streams = [s for s in self.streams if s.get("id") != stream_id]
        if self.active_id == stream_id:
            self.active_id = self.streams[0]["id"] if self.streams else None
        if len(self.streams) == before:
            return {"success": False, "error": "not_found"}
        return {"success": True, "active": self.active_id, "streams": self.streams}

    def set_active(self, stream_id: str) -> dict:
        for s in self.streams:
            if s.get("id") == stream_id:
                self.active_id = stream_id
                s["status"] = "active"
                for other in self.streams:
                    if other["id"] != stream_id and other.get("status") == "active":
                        other["status"] = "idle"
                return {"success": True, "stream": s, "active": self.active_id}
        return {"success": False, "error": "not_found"}

    def set_comment(self, stream_id: str, comment: str) -> dict:
        for s in self.streams:
            if s.get("id") == stream_id:
                s["last_comment"] = comment
                return {"success": True, "stream": s}
        return {"success": False, "error": "not_found"}

    def get(self, stream_id: str | None = None) -> dict | None:
        sid = stream_id or self.active_id
        if not sid:
            return None
        for s in self.streams:
            if s.get("id") == sid:
                return s
        return None
