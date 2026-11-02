
"""Local approval store for PRIVILEGED bridge tasks (not exposed via Funnel)."""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nexus.bridge.errors import (
    BridgeError,
    APPROVAL_DENIED,
    APPROVAL_EXPIRED,
    APPROVAL_NOT_FOUND,
    APPROVAL_REQUIRED,
    INVALID_REQUEST,
)
from nexus.common.paths import WORKSPACE

APPROVAL_TTL_MIN = 30
STORE_DIR = WORKSPACE / "bridge" / "approvals"


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds")


class ApprovalRecord:
    __slots__ = (
        "approval_id", "task", "params", "status", "reason",
        "created_at", "expires_at", "decided_at", "decided_by", "task_id",
    )

    def __init__(self, data: dict[str, Any]) -> None:
        self.approval_id = data["approval_id"]
        self.task = data.get("task") or ""
        self.params = data.get("params") or {}
        self.status = data.get("status") or "pending"  # pending|approved|denied|expired|consumed
        self.reason = data.get("reason") or ""
        self.created_at = data.get("created_at") or _iso()
        self.expires_at = data.get("expires_at") or ""
        self.decided_at = data.get("decided_at")
        self.decided_by = data.get("decided_by")
        self.task_id = data.get("task_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "task": self.task,
            "params": self.params,
            "status": self.status,
            "reason": self.reason,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "task_id": self.task_id,
        }


class ApprovalStore:
    def __init__(self, persist_dir: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, ApprovalRecord] = {}
        self.persist_dir = persist_dir or STORE_DIR
        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._load_all()

    def _path(self, aid: str) -> Path:
        safe = "".join(c for c in aid if c.isalnum() or c in "-_")
        return self.persist_dir / f"{safe}.json"

    def _persist(self, rec: ApprovalRecord) -> None:
        try:
            self._path(rec.approval_id).write_text(
                json.dumps(rec.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load_all(self) -> None:
        if not self.persist_dir.is_dir():
            return
        for fp in self.persist_dir.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                rec = ApprovalRecord(data)
                self._items[rec.approval_id] = rec
            except Exception:
                continue

    def request(self, task: str, params: dict[str, Any], reason: str = "") -> ApprovalRecord:
        aid = "AP-" + secrets.token_hex(8)
        exp = _now() + timedelta(minutes=APPROVAL_TTL_MIN)
        rec = ApprovalRecord({
            "approval_id": aid,
            "task": task,
            "params": dict(params or {}),
            "status": "pending",
            "reason": reason or f"PRIVILEGED task '{task}' requires local approval",
            "created_at": _iso(),
            "expires_at": _iso(exp),
        })
        with self._lock:
            self._items[aid] = rec
            self._persist(rec)
        return rec

    def get(self, approval_id: str) -> ApprovalRecord:
        with self._lock:
            rec = self._items.get(approval_id)
            if not rec:
                fp = self._path(approval_id)
                if fp.is_file():
                    try:
                        rec = ApprovalRecord(json.loads(fp.read_text(encoding="utf-8")))
                        self._items[approval_id] = rec
                    except Exception:
                        rec = None
            if not rec:
                raise BridgeError(APPROVAL_NOT_FOUND, f"approval not found: {approval_id}", http_status=404)
            self._expire_if_needed(rec)
            return rec

    def _expire_if_needed(self, rec: ApprovalRecord) -> None:
        if rec.status != "pending":
            return
        try:
            exp = datetime.fromisoformat(rec.expires_at)
            if _now() > exp:
                rec.status = "expired"
                rec.decided_at = _iso()
                self._persist(rec)
        except Exception:
            pass

    def decide(self, approval_id: str, approve: bool, decided_by: str = "local_ui") -> ApprovalRecord:
        with self._lock:
            rec = self.get(approval_id)
            if rec.status == "expired":
                raise BridgeError(APPROVAL_EXPIRED, "approval expired", http_status=410)
            if rec.status in ("approved", "denied", "consumed"):
                raise BridgeError(INVALID_REQUEST, f"approval already {rec.status}", http_status=409)
            rec.status = "approved" if approve else "denied"
            rec.decided_at = _iso()
            rec.decided_by = decided_by or "local_ui"
            self._persist(rec)
            return rec

    def consume(self, approval_id: str, task: str, task_id: str | None = None) -> ApprovalRecord:
        """Validate approved approval for task and mark consumed (one-shot)."""
        with self._lock:
            if not approval_id:
                raise BridgeError(
                    APPROVAL_REQUIRED,
                    f"PRIVILEGED task '{task}' requires approval_id",
                    http_status=403,
                    details={"task": task},
                )
            rec = self.get(approval_id)
            if rec.status == "expired":
                raise BridgeError(APPROVAL_EXPIRED, "approval expired", http_status=410)
            if rec.status == "denied":
                raise BridgeError(APPROVAL_DENIED, "approval denied", http_status=403)
            if rec.status == "pending":
                raise BridgeError(
                    APPROVAL_REQUIRED,
                    "approval still pending — open local Approval UI",
                    http_status=403,
                    details={"approval_id": approval_id, "ui": "/approvals"},
                )
            if rec.status == "consumed":
                raise BridgeError(APPROVAL_DENIED, "approval already consumed", http_status=403)
            if rec.task and rec.task != task:
                raise BridgeError(
                    APPROVAL_DENIED,
                    f"approval is for task '{rec.task}', not '{task}'",
                    http_status=403,
                )
            rec.status = "consumed"
            rec.task_id = task_id
            rec.decided_at = rec.decided_at or _iso()
            self._persist(rec)
            return rec

    def list(self, limit: int = 50, status: str | None = None) -> list[ApprovalRecord]:
        with self._lock:
            items = list(self._items.values())
        for rec in items:
            self._expire_if_needed(rec)
        if status:
            items = [i for i in items if i.status == status]
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items[: max(1, min(limit, 200))]


_STORE: ApprovalStore | None = None


def approvals() -> ApprovalStore:
    global _STORE
    if _STORE is None:
        _STORE = ApprovalStore()
    return _STORE
