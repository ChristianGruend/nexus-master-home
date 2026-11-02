from __future__ import annotations
import json
from datetime import datetime
from nexus.common.paths import TASKS_DIR, ensure_dirs

DATA = TASKS_DIR / "tasks.json"
STATUSES = ("Active", "Wartend", "Abgeschlossen")

class TaskCenter:
    def __init__(self) -> None:
        ensure_dirs()
        self.tasks = self._load()

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _load(self) -> dict:
        if not DATA.exists():
            return {}
        try:
            return json.loads(DATA.read_text(encoding="utf-8")).get("tasks", {})
        except Exception:
            return {}

    def _save(self) -> None:
        DATA.write_text(json.dumps({"updated": self._now(), "tasks": self.tasks}, indent=2, ensure_ascii=False), encoding="utf-8")

    def list(self, status: str | None = None) -> list:
        items = list(self.tasks.values())
        if status:
            items = [t for t in items if t.get("status") == status]
        return sorted(items, key=lambda t: t.get("updated", ""), reverse=True)

    def create(self, title: str, plan: list | None = None, status: str = "Active") -> dict:
        tid = f"T-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        t = {
            "id": tid, "title": title.strip() or "Task",
            "status": status if status in STATUSES else "Active",
            "plan": plan or [], "result": None,
            "created": self._now(), "updated": self._now(),
        }
        self.tasks[tid] = t
        self._save()
        return {"success": True, "task": t}

    def update(self, tid: str, **fields) -> dict:
        t = self.tasks.get(tid)
        if not t:
            return {"success": False, "error": "not_found"}
        if "status" in fields and fields["status"] in STATUSES:
            t["status"] = fields["status"]
        for k in ("title", "plan", "result"):
            if k in fields and fields[k] is not None:
                t[k] = fields[k]
        t["updated"] = self._now()
        self._save()
        return {"success": True, "task": t}

    def get(self, tid: str) -> dict | None:
        return self.tasks.get(tid)
