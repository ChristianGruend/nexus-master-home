"""Live activity ring buffer — Was Nexus gerade macht."""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


class ActivityLog:
    """Thread-safe ring buffer for live agent/tool progress."""

    def __init__(self, maxlen: int = 200) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._current: dict[str, Any] = {
            "phase": "idle",
            "intent": None,
            "text": "Bereit",
            "ts": time.time(),
        }

    def push(self, event: dict[str, Any] | None) -> dict[str, Any]:
        if not event:
            return self.snapshot()
        item = {
            "ts": time.time(),
            "phase": event.get("phase") or "info",
            "intent": event.get("intent"),
            "text": (event.get("text") or "").strip(),
            "tools": event.get("tools") or [],
            "ok": event.get("ok"),
            "plan": event.get("plan"),
        }
        if not item["text"] and item["tools"]:
            item["text"] = "Tools: " + ", ".join(str(t) for t in item["tools"])
        try:  # Live-Monitor (20261028)
            from nexus.common.live_events import emit as _emit
            if item["text"] or item["tools"]:
                _emit("NEXUS", str(item["phase"]), item["text"], ok=item["ok"],
                      data={"intent": item["intent"], "tools": item["tools"]} if (item["intent"] or item["tools"]) else None)
        except Exception:
            pass
        with self._lock:
            if item["text"] or item["tools"] or item["phase"] in ("intent", "start", "idle", "done"):
                self._items.append(item)
            if item["phase"] != "info" or item["text"]:
                self._current = {
                    "phase": item["phase"],
                    "intent": item.get("intent") or self._current.get("intent"),
                    "text": item["text"] or self._current.get("text") or "…",
                    "tools": item.get("tools") or [],
                    "ts": item["ts"],
                }
            if item["phase"] in ("idle", "done"):
                self._current["phase"] = "idle"
        return item

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._current = {
                "phase": "idle",
                "intent": None,
                "text": "Bereit",
                "ts": time.time(),
            }

    def snapshot(self, limit: int = 100) -> dict[str, Any]:
        with self._lock:
            items = list(self._items)[-max(1, min(limit, 200)) :]
            current = dict(self._current)
        return {
            "success": True,
            "current": current,
            "items": items,
            "count": len(items),
        }


# Process-wide singleton (shared by MasterController)
_LOG = ActivityLog()


def get_activity_log() -> ActivityLog:
    return _LOG
