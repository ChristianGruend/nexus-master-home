"""Local-only automation scheduler.

- Never exposed via Funnel/Gateway allowlist
- No uncontrolled shell loops: at most one Bridge task per due routine per tick
- PRIVILEGED never auto-scheduled
- Default: stopped until explicitly started locally
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from nexus.automation.routines import routines
from nexus.bridge.permissions import PermissionLevel

TICK_SEC = 15
MAX_PER_TICK = 1


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class LocalScheduler:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._running = False
        self._last_tick: str | None = None
        self._last_error: str | None = None
        self._runs = 0

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "local_only": True,
                "funnel_exposed": False,
                "tick_sec": TICK_SEC,
                "max_per_tick": MAX_PER_TICK,
                "last_tick": self._last_tick,
                "last_error": self._last_error,
                "runs": self._runs,
                "note": "Scheduler binds to origin :8299 only; NOT on gateway allowlist",
            }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._running:
                return self.status()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="nexus-automation-local", daemon=True
            )
            self._running = True
            self._thread.start()
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._stop.set()
            self._running = False
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=3.0)
        with self._lock:
            self._thread = None
            return self.status()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                with self._lock:
                    self._last_error = str(e)
            self._stop.wait(TICK_SEC)

    def _tick(self) -> None:
        store = routines()
        due = store.due_interval()
        with self._lock:
            self._last_tick = _now_iso()
        ran = 0
        for rec in due:
            if ran >= MAX_PER_TICK:
                break
            if rec.security_class == PermissionLevel.PRIVILEGED.value:
                continue
            try:
                store.run(rec.id, trigger_override="interval")
                ran += 1
                with self._lock:
                    self._runs += 1
                    self._last_error = None
            except Exception as e:
                with self._lock:
                    self._last_error = f"{rec.id}: {e}"


_SCHED: LocalScheduler | None = None


def scheduler() -> LocalScheduler:
    global _SCHED
    if _SCHED is None:
        _SCHED = LocalScheduler()
    return _SCHED
