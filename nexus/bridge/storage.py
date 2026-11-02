"""In-memory + optional JSON persistence for bridge tasks."""
from __future__ import annotations

import json
import os
import copy
import threading
from pathlib import Path
from typing import Any

from nexus.common.paths import WORKSPACE
from nexus.bridge.models import TaskRecord


class TaskStorage:
    def __init__(self, persist_dir: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, TaskRecord] = {}
        self.persist_dir = persist_dir or (WORKSPACE / "bridge" / "tasks")
        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def put(self, record: TaskRecord) -> None:
        with self._lock:
            self._persist(record)
            self._tasks[record.id] = copy.deepcopy(record)

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            # Persisted task records are authoritative across bridge/process
            # restarts and controlled metadata recovery. Refresh from disk when
            # present so an old in-memory 'running' record cannot mask a
            # terminal recovery written after restart.
            fp = self._path(task_id)
            if fp.is_file():
                return self._load(task_id)
            rec = self._tasks.get(task_id)
            return copy.deepcopy(rec) if rec else None

    def list(self, limit: int = 50) -> list[TaskRecord]:
        with self._lock:
            items = list(self._tasks.values())
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items[: max(1, min(limit, 200))]

    def _path(self, task_id: str) -> Path:
        safe = "".join(c for c in task_id if c.isalnum() or c in "-_")
        return self.persist_dir / f"{safe}.json"

    def _persist(self, record: TaskRecord) -> None:
        fp = self._path(record.id)
        temporary = fp.with_suffix('.tmp')
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(record.to_dict(include_log=True), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, fp)

    def _load(self, task_id: str) -> TaskRecord | None:
        fp = self._path(task_id)
        if not fp.is_file():
            return None
        try:
            # Accept UTF-8 task files with or without BOM; reads never rewrite evidence.
            data: dict[str, Any] = json.loads(fp.read_text(encoding="utf-8-sig"))
            rec = TaskRecord(
                id=data["id"],
                task=data.get("task") or "",
                status=data.get("status") or "unknown",
                permission=data.get("permission") or "READ_ONLY",
                created_at=data.get("created_at") or "",
                updated_at=data.get("updated_at") or "",
                params=data.get("params") or {},
                result=data.get("result"),
                error=data.get("error"),
                error_code=data.get("error_code"),
                log=list(data.get("log") or []),
                source=data.get("source"),
                client=data.get("client"),
                session_id=data.get("session_id"),
                approval_id=data.get("approval_id"),
                execution=data.get("execution"),
            )
            self._tasks[rec.id] = rec
            return rec
        except Exception as exc:
            raise RuntimeError("TASK_RECORD_UNREADABLE:" + task_id) from exc
