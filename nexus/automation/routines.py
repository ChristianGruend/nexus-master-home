"""Controlled automations: ID, status, trigger, action, security class, logs, error, cancel.

Backbone = Bridge TaskManager (registered tasks only). No competing executor.
No uncontrolled shell loops. PRIVILEGED requires local approval_id via /approvals.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.bridge.models import TaskCreateRequest
from nexus.bridge.permissions import TASK_PERMISSIONS, PermissionLevel
from nexus.common.paths import WORKSPACE

STORE_DIR = WORKSPACE / "automation" / "routines"
RUNS_DIR = WORKSPACE / "automation" / "runs"
MIN_INTERVAL_SEC = 60
MAX_INTERVAL_SEC = 86400
ALLOWED_TRIGGERS = ("manual", "api", "interval")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_id(raw: str) -> str:
    return "".join(c for c in raw if c.isalnum() or c in "-_")


@dataclass
class Routine:
    id: str
    name: str
    status: str
    trigger: str
    action: str
    security_class: str
    created_at: str
    updated_at: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    interval_sec: int | None = None
    last_run_at: str | None = None
    last_task_id: str | None = None
    error: str | None = None
    error_code: str | None = None
    log: list[str] = field(default_factory=list)
    approval_id: str | None = None
    source: str = "local"
    note: str = ""

    def to_dict(self, include_log: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_log:
            d.pop("log", None)
        return d


class AutomationStore:
    """Named routines that delegate execution to Bridge tasks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, Routine] = {}
        self._seq = 0
        self._day = datetime.now().strftime("%Y%m%d")
        self._cancel: dict[str, bool] = {}
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        self._load_all()
        if not self._items:
            self._seed_defaults()

    def _next_id(self) -> str:
        with self._lock:
            day = datetime.now().strftime("%Y%m%d")
            if day != self._day:
                self._day = day
                self._seq = 0
            self._seq += 1
            return f"AU-{day}-{self._seq:04d}"

    def _path(self, rid: str) -> Path:
        return STORE_DIR / f"{_safe_id(rid)}.json"

    def _persist(self, rec: Routine) -> None:
        try:
            self._path(rec.id).write_text(
                json.dumps(rec.to_dict(include_log=True), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load_all(self) -> None:
        if not STORE_DIR.is_dir():
            return
        for fp in STORE_DIR.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                rec = self._from_dict(data)
                self._items[rec.id] = rec
            except Exception:
                continue

    def _from_dict(self, data: dict[str, Any]) -> Routine:
        return Routine(
            id=data["id"],
            name=data.get("name") or data["id"],
            status=data.get("status") or "idle",
            trigger=data.get("trigger") or "manual",
            action=data.get("action") or "",
            security_class=data.get("security_class") or "READ_ONLY",
            created_at=data.get("created_at") or "",
            updated_at=data.get("updated_at") or "",
            params=data.get("params") or {},
            enabled=bool(data.get("enabled", True)),
            interval_sec=data.get("interval_sec"),
            last_run_at=data.get("last_run_at"),
            last_task_id=data.get("last_task_id"),
            error=data.get("error"),
            error_code=data.get("error_code"),
            log=list(data.get("log") or []),
            approval_id=data.get("approval_id"),
            source=data.get("source") or "local",
            note=data.get("note") or "",
        )

    def _seed_defaults(self) -> None:
        now = _now_iso()
        seeds = [
            Routine(
                id="AU-default-ping",
                name="Bridge Ping (READ_ONLY)",
                status="idle",
                trigger="manual",
                action="ping",
                security_class="READ_ONLY",
                created_at=now,
                updated_at=now,
                params={"echo": "automation-default"},
                enabled=True,
                note="Seed: safe READ_ONLY smoke via Bridge ping",
                log=[f"{now} seeded"],
            ),
            Routine(
                id="AU-default-nexus-status",
                name="Nexus Status (READ_ONLY)",
                status="idle",
                trigger="manual",
                action="nexus_status",
                security_class="READ_ONLY",
                created_at=now,
                updated_at=now,
                params={},
                enabled=True,
                note="Seed: local health/status probe via Bridge",
                log=[f"{now} seeded"],
            ),
            Routine(
                id="AU-default-health-interval",
                name="Local Health Interval (disabled)",
                status="disabled",
                trigger="interval",
                action="ping",
                security_class="READ_ONLY",
                created_at=now,
                updated_at=now,
                params={"echo": "scheduler-local"},
                enabled=False,
                interval_sec=300,
                note="Seed: interval example; disabled by default; LOCAL scheduler only",
                log=[f"{now} seeded disabled"],
            ),
        ]
        for rec in seeds:
            self._items[rec.id] = rec
            self._persist(rec)

    def list(self, limit: int = 100) -> list[Routine]:
        with self._lock:
            items = list(self._items.values())
        items.sort(key=lambda r: r.updated_at, reverse=True)
        return items[: max(1, min(limit, 200))]

    def get(self, rid: str) -> Routine | None:
        with self._lock:
            rec = self._items.get(rid)
            if rec:
                return rec
        fp = self._path(rid)
        if not fp.is_file():
            return None
        try:
            rec = self._from_dict(json.loads(fp.read_text(encoding="utf-8")))
            with self._lock:
                self._items[rec.id] = rec
            return rec
        except Exception:
            return None

    def create(
        self,
        *,
        name: str,
        action: str,
        trigger: str = "manual",
        params: dict[str, Any] | None = None,
        interval_sec: int | None = None,
        enabled: bool = True,
        note: str = "",
        approval_id: str | None = None,
    ) -> Routine:
        action = (action or "").strip()
        if action not in TASK_PERMISSIONS:
            raise ValueError(
                f"Unknown bridge action '{action}'. Use registered Bridge tasks only."
            )
        trigger = (trigger or "manual").strip().lower()
        if trigger not in ALLOWED_TRIGGERS:
            raise ValueError(f"trigger must be one of {ALLOWED_TRIGGERS}")
        sec = TASK_PERMISSIONS[action]
        iv = None
        if trigger == "interval":
            try:
                iv = int(interval_sec if interval_sec is not None else 300)
            except Exception as e:
                raise ValueError("interval_sec required for interval trigger") from e
            iv = max(MIN_INTERVAL_SEC, min(MAX_INTERVAL_SEC, iv))
            if sec == PermissionLevel.PRIVILEGED:
                raise ValueError(
                    "PRIVILEGED actions cannot use interval trigger; use manual+approval"
                )
        now = _now_iso()
        rec = Routine(
            id=self._next_id(),
            name=(name or action).strip() or action,
            status="idle" if enabled else "disabled",
            trigger=trigger,
            action=action,
            security_class=sec.value,
            created_at=now,
            updated_at=now,
            params=dict(params or {}),
            enabled=bool(enabled),
            interval_sec=iv,
            approval_id=approval_id,
            note=note or "",
            log=[f"{now} created action={action} trigger={trigger} class={sec.value}"],
        )
        with self._lock:
            self._items[rec.id] = rec
            self._persist(rec)
        return rec

    def set_enabled(self, rid: str, enabled: bool) -> Routine:
        rec = self.get(rid)
        if not rec:
            raise KeyError(rid)
        with self._lock:
            rec.enabled = bool(enabled)
            rec.status = "idle" if enabled else "disabled"
            rec.updated_at = _now_iso()
            rec.log.append(f"{rec.updated_at} enabled={rec.enabled}")
            self._persist(rec)
        return rec

    def cancel(self, rid: str) -> Routine:
        rec = self.get(rid)
        if not rec:
            raise KeyError(rid)
        self._cancel[rid] = True
        with self._lock:
            if rec.status in ("running", "idle", "awaiting_approval"):
                rec.status = "cancelled"
                rec.error_code = "AUTOMATION_CANCELLED"
                rec.error = "cancelled by client"
                rec.updated_at = _now_iso()
                rec.log.append(f"{rec.updated_at} cancelled")
                self._persist(rec)
            elif rec.status != "cancelled":
                rec.status = "cancelled"
                rec.error_code = rec.error_code or "AUTOMATION_CANCELLED"
                rec.error = rec.error or "cancelled after terminal state"
                rec.updated_at = _now_iso()
                rec.log.append(f"{rec.updated_at} cancelled (was terminal)")
                self._persist(rec)
            if rec.last_task_id:
                try:
                    from nexus.bridge import api as bridge_api
                    bridge_api.manager().cancel(rec.last_task_id)
                except Exception:
                    pass
        return rec

    def run(
        self,
        rid: str,
        *,
        trigger_override: str | None = None,
        approval_id: str | None = None,
    ) -> Routine:
        """Execute routine via Bridge TaskManager (single shot, no shell loop)."""
        rec = self.get(rid)
        if not rec:
            raise KeyError(rid)
        if not rec.enabled and (trigger_override or rec.trigger) == "interval":
            raise RuntimeError("routine disabled")
        action = rec.action
        if action not in TASK_PERMISSIONS:
            raise ValueError(f"Unknown bridge action '{action}'")
        sec = TASK_PERMISSIONS[action]
        trig = (trigger_override or rec.trigger or "manual").lower()
        if trig == "interval" and sec == PermissionLevel.PRIVILEGED:
            raise RuntimeError("PRIVILEGED blocked from interval scheduler")

        self._cancel[rid] = False
        now = _now_iso()
        with self._lock:
            rec.status = "running"
            rec.error = None
            rec.error_code = None
            rec.updated_at = now
            rec.log.append(f"{now} run start trigger={trig} action={action}")
            self._persist(rec)

        if self._cancel.get(rid):
            return self.cancel(rid)

        from nexus.bridge import api as bridge_api

        aid = approval_id or rec.approval_id
        req = TaskCreateRequest(
            task=action,
            params=dict(rec.params or {}),
            source=f"automation:{trig}",
            client="nexus.automation",
            approval_id=str(aid) if aid else None,
        )
        try:
            task_rec = bridge_api.manager().create_and_run(req)
        except Exception as e:
            with self._lock:
                rec.status = "failed"
                rec.error_code = "AUTOMATION_FAILED"
                rec.error = str(e)
                rec.updated_at = _now_iso()
                rec.log.append(f"{rec.updated_at} failed: {e}")
                self._persist(rec)
            return rec

        with self._lock:
            rec.last_task_id = task_rec.id
            rec.last_run_at = _now_iso()
            rec.approval_id = task_rec.approval_id or rec.approval_id
            if self._cancel.get(rid):
                rec.status = "cancelled"
                rec.error_code = "AUTOMATION_CANCELLED"
                rec.error = "cancelled during run"
            elif task_rec.status == "awaiting_approval":
                rec.status = "awaiting_approval"
                rec.error_code = None
                rec.error = None
            elif task_rec.status == "succeeded":
                rec.status = "succeeded"
                rec.error = None
                rec.error_code = None
            elif task_rec.status == "cancelled":
                rec.status = "cancelled"
                rec.error_code = task_rec.error_code
                rec.error = task_rec.error
            else:
                rec.status = "failed"
                rec.error_code = task_rec.error_code or "TASK_FAILED"
                rec.error = task_rec.error or task_rec.status
            rec.updated_at = _now_iso()
            rec.log.append(
                f"{rec.updated_at} bridge_task={task_rec.id} status={rec.status}"
            )
            if len(rec.log) > 200:
                rec.log = rec.log[-200:]
            self._persist(rec)
            try:
                snap = {
                    "routine_id": rec.id,
                    "task_id": task_rec.id,
                    "action": action,
                    "security_class": rec.security_class,
                    "trigger": trig,
                    "status": rec.status,
                    "error_code": rec.error_code,
                    "at": rec.updated_at,
                }
                (RUNS_DIR / f"{_safe_id(rec.id)}_{_safe_id(task_rec.id)}.json").write_text(
                    json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass
        return rec

    def due_interval(self) -> list[Routine]:
        """Routines eligible for local scheduler tick (no PRIVILEGED)."""
        now = datetime.now().astimezone()
        out: list[Routine] = []
        for rec in self.list(limit=200):
            if not rec.enabled or rec.trigger != "interval":
                continue
            if rec.security_class == PermissionLevel.PRIVILEGED.value:
                continue
            if rec.status == "running":
                continue
            iv = int(rec.interval_sec or 300)
            iv = max(MIN_INTERVAL_SEC, min(MAX_INTERVAL_SEC, iv))
            if rec.last_run_at:
                try:
                    last = datetime.fromisoformat(rec.last_run_at)
                    if (now - last).total_seconds() < iv:
                        continue
                except Exception:
                    pass
            out.append(rec)
        return out


_STORE: AutomationStore | None = None


def routines() -> AutomationStore:
    global _STORE
    if _STORE is None:
        _STORE = AutomationStore()
    return _STORE
