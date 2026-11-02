
"""Bridge session tracking for task lifecycle."""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.common.paths import WORKSPACE

SESS_DIR = WORKSPACE / "bridge" / "sessions"


def _iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self.persist_dir = SESS_DIR
        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def create(self, client: str | None = None, source: str | None = None) -> dict[str, Any]:
        sid = "BS-" + secrets.token_hex(6)
        rec = {
            "session_id": sid,
            "created_at": _iso(),
            "updated_at": _iso(),
            "client": client,
            "source": source,
            "task_ids": [],
            "message_count": 0,
        }
        with self._lock:
            self._sessions[sid] = rec
            self._persist(rec)
        return dict(rec)

    def touch(self, session_id: str, task_id: str | None = None, message: bool = False) -> dict[str, Any] | None:
        with self._lock:
            rec = self._sessions.get(session_id)
            if not rec:
                return None
            rec["updated_at"] = _iso()
            if task_id:
                ids = list(rec.get("task_ids") or [])
                if task_id not in ids:
                    ids.append(task_id)
                rec["task_ids"] = ids[-100:]
            if message:
                rec["message_count"] = int(rec.get("message_count") or 0) + 1
            self._persist(rec)
            return dict(rec)

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._sessions.get(session_id)
            return dict(rec) if rec else None

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._sessions.values())
        items.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
        return [dict(i) for i in items[:limit]]

    def _persist(self, rec: dict[str, Any]) -> None:
        try:
            sid = rec["session_id"]
            safe = "".join(c for c in sid if c.isalnum() or c in "-_")
            (self.persist_dir / f"{safe}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass


_SESS: SessionStore | None = None


def sessions() -> SessionStore:
    global _SESS
    if _SESS is None:
        _SESS = SessionStore()
    return _SESS
