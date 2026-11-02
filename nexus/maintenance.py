"""Local, authenticated maintenance / drain gate for NEXUS.

Goal
----
Provide an atomic *drain* contract for the running server so that a verified,
operator-controlled stop/restart does not silently discard running work:

1. New work ("Auftraege") is blocked while a drain is in progress.
2. Work that already started keeps running and is counted until it finishes.
3. Only when nothing is in flight may a graceful shutdown be requested.

Security boundaries (unchanged)
-------------------------------
* The maintenance control endpoints live at ``/api/bridge/maintenance*``.
  This path is intentionally **not** part of the bridge gateway allowlist, so
  it is never reachable through the Funnel / gateway.
* Callers must present the Agent-Bridge Bearer token (separate from the
  assistant token) **and** originate from loopback.
* This module never executes registered tasks and does not touch the
  READ_ONLY / SAFE_WRITE / PRIVILEGED classification. It cannot be used to
  obtain an approval or to run anything with elevated permission.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from nexus.common.paths import WORKSPACE

STATE_PATH = WORKSPACE / "logs" / "nexus_maintenance.json"

STATE_RUNNING = "RUNNING"
STATE_DRAINING = "DRAINING"
STATE_DRAINED = "DRAINED"

DEFAULT_DRAIN_TIMEOUT = 300.0
DEFAULT_QUIET_SECONDS = 2.0


class MaintenanceActive(RuntimeError):
    """Raised when new work is submitted while a drain is active."""


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time()).astimezone().isoformat(
        timespec="seconds"
    )


class MaintenanceController:
    def __init__(self, state_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._draining = False
        self._in_flight = 0
        self._active: dict[str, int] = {}
        self._reason = ""
        self._actor = ""
        self._started_ts: float | None = None
        self._drained_ts: float | None = None
        self._shutdown_cb: Callable[[], None] | None = None
        self._shutdown_requested = False
        self._state_path = state_path or STATE_PATH
        self._persist_locked()

    # -- callbacks ---------------------------------------------------------
    def set_shutdown_callback(self, cb: Callable[[], None]) -> None:
        with self._lock:
            self._shutdown_cb = cb

    # -- introspection -----------------------------------------------------
    def is_draining(self) -> bool:
        with self._lock:
            return self._draining

    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def state(self) -> str:
        with self._lock:
            if not self._draining:
                return STATE_RUNNING
            if self._in_flight <= 0 and self._drained_ts is not None:
                return STATE_DRAINED
            return STATE_DRAINING

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "state": (
                STATE_RUNNING
                if not self._draining
                else (STATE_DRAINED if self._in_flight <= 0 and self._drained_ts is not None
                      else STATE_DRAINING)
            ),
            "draining": self._draining,
            "in_flight": self._in_flight,
            "active": dict(self._active),
            "reason": self._reason,
            "actor": self._actor,
            "started_at": _iso(self._started_ts) if self._started_ts else None,
            "drained_at": _iso(self._drained_ts) if self._drained_ts else None,
            "shutdown_requested": self._shutdown_requested,
            "updated_at": _iso(),
        }

    # -- work accounting ---------------------------------------------------
    @contextmanager
    def work(self, kind: str) -> Iterator[None]:
        """Register in-flight work. Raises MaintenanceActive if a drain is active."""
        with self._cond:
            if self._draining:
                raise MaintenanceActive(
                    "maintenance drain active: new work is blocked"
                )
            self._in_flight += 1
            self._active[kind] = self._active.get(kind, 0) + 1
        try:
            yield
        finally:
            with self._cond:
                self._in_flight -= 1
                if self._in_flight < 0:
                    self._in_flight = 0
                remaining = self._active.get(kind, 0) - 1
                if remaining <= 0:
                    self._active.pop(kind, None)
                else:
                    self._active[kind] = remaining
                if self._in_flight <= 0 and self._draining and self._drained_ts is None:
                    self._drained_ts = time.time()
                self._cond.notify_all()
                self._persist_locked()

    # -- drain lifecycle ---------------------------------------------------
    def begin_drain(
        self, reason: str = "", actor: str = "local", timeout: float | None = None
    ) -> dict[str, Any]:
        with self._cond:
            self._draining = True
            self._reason = str(reason or "")
            self._actor = str(actor or "local")
            self._started_ts = time.time()
            self._drained_ts = None
            if self._in_flight <= 0:
                self._drained_ts = time.time()
            self._persist_locked()
            return {
                **self._snapshot_locked(),
                "timeout": float(timeout) if timeout is not None else DEFAULT_DRAIN_TIMEOUT,
                "already": False,
            }

    def cancel_drain(self, actor: str = "local") -> dict[str, Any]:
        with self._cond:
            self._draining = False
            self._drained_ts = None
            self._reason = ""
            self._actor = str(actor or "local")
            self._started_ts = None
            self._shutdown_requested = False
            self._cond.notify_all()
            self._persist_locked()
            return self._snapshot_locked()

    def wait_drained(
        self, timeout: float | None = None, quiet_seconds: float = DEFAULT_QUIET_SECONDS
    ) -> bool:
        """Wait until in_flight stays 0 for `quiet_seconds`. Returns True if drained."""
        deadline = time.time() + (
            float(timeout) if timeout is not None else DEFAULT_DRAIN_TIMEOUT
        )
        required = max(0.0, float(quiet_seconds))
        with self._cond:
            while True:
                if not self._draining:
                    return False
                if self._in_flight <= 0:
                    idle_since = time.time()
                    while self._in_flight <= 0:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            break
                        wait_for = min(required - (time.time() - idle_since), remaining)
                        if wait_for <= 0:
                            break
                        self._cond.wait(timeout=wait_for)
                    if self._in_flight <= 0 and (time.time() - idle_since) >= required:
                        if self._drained_ts is None:
                            self._drained_ts = time.time()
                            self._persist_locked()
                        return True
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=min(remaining, 5.0))

    def request_shutdown(self, actor: str = "local", confirm: bool = False) -> dict[str, Any]:
        """Request graceful server shutdown. Allowed only after a completed drain."""
        with self._lock:
            if not confirm:
                return {"ok": False, "error": "confirmation_required", **self._snapshot_locked()}
            if self._in_flight > 0:
                return {
                    "ok": False,
                    "error": "work_in_flight",
                    **self._snapshot_locked(),
                }
            if not self._draining:
                return {"ok": False, "error": "drain_not_active", **self._snapshot_locked()}
            self._shutdown_requested = True
            self._actor = str(actor or "local")
            self._persist_locked()
            cb = self._shutdown_cb
            snap = self._snapshot_locked()
        if cb is None:
            return {"ok": False, "error": "shutdown_unavailable", **snap}
        cb()
        return {"ok": True, "scheduled": True, **snap}

    # -- persistence -------------------------------------------------------
    def _persist_locked(self) -> None:
        snap = self._snapshot_locked()
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass


_CONTROLLER: MaintenanceController | None = None


def maintenance() -> MaintenanceController:
    global _CONTROLLER
    if _CONTROLLER is None:
        _CONTROLLER = MaintenanceController()
    return _CONTROLLER


def block_payload() -> dict[str, Any]:
    snap = maintenance().snapshot()
    return {
        "success": False,
        "error": "MAINTENANCE_ACTIVE",
        "message": "NEXUS maintenance drain active; new work is blocked until restart.",
        "maintenance": snap,
    }
