from __future__ import annotations
from nexus.common.paths import MEDIA_DIR, ensure_dirs
from .playlist import scan

class MediaPlayer:
    """Stateful Playlist — Audio spielt im Browser."""

    def __init__(self) -> None:
        ensure_dirs()
        self.items: list = []
        self.index: int = 0
        self.playing: bool = False
        self.refresh()

    def refresh(self) -> None:
        # nexus_media_filter (Nexus-Media 02.11.2026): TTS-Ausgaben und versteckte Ordner (.papierkorb) raus
        self.items = [it for it in scan(MEDIA_DIR)
                      if not any(seg.startswith('.') or seg.lower() == 'tts'
                                 for seg in str(it.get('path', '')).replace('\\', '/').split('/')[:-1])]
        if self.index >= len(self.items):
            self.index = 0

    def state(self) -> dict:
        cur = self.items[self.index] if self.items else None
        return {
            "playing": self.playing,
            "index": self.index,
            "count": len(self.items),
            "current": cur,
            "items": self.items,
        }

    def play(self) -> dict:
        self.refresh()
        self.playing = True
        return {"success": True, "action": "play", "state": self.state(), "reply": "Play."}

    def resume(self) -> dict:
        """Alias von play — UI/Smoke erwarten /api/media/resume nach pause."""
        self.refresh()
        self.playing = True
        return {"success": True, "action": "resume", "state": self.state(), "reply": "Weiter."}

    def play_at(self, index: int) -> dict:
        self.refresh()
        if not self.items:
            return {"success": False, "error": "empty", "reply": "Keine Tracks.", "state": self.state()}
        self.index = int(index) % len(self.items)
        self.playing = True
        cur = self.items[self.index]
        return {"success": True, "action": "play_at", "state": self.state(), "reply": f"Spiele: {cur.get('name')}"}

    def pause(self) -> dict:
        self.playing = False
        return {"success": True, "action": "pause", "state": self.state(), "reply": "Pause."}

    def next(self) -> dict:
        self.refresh()
        if self.items:
            self.index = (self.index + 1) % len(self.items)
            self.playing = True
        return {"success": True, "action": "next", "state": self.state(), "reply": "Nächster Track."}

    def prev(self) -> dict:
        self.refresh()
        if self.items:
            self.index = (self.index - 1) % len(self.items)
            self.playing = True
        return {"success": True, "action": "prev", "state": self.state(), "reply": "Vorheriger Track."}

    def stop(self) -> dict:
        self.playing = False
        return {"success": True, "action": "stop", "state": self.state(), "reply": "Stop."}
