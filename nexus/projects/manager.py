from __future__ import annotations
import json
from datetime import datetime
from nexus.common.paths import PROJECTS_DIR, ensure_dirs

DATA = PROJECTS_DIR / "projects.json"
ACTIVE = PROJECTS_DIR / "active.json"

class ProjectManager:
    def __init__(self) -> None:
        ensure_dirs()
        self.projects = self._load()
        self.active_id = self._load_active()

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _load(self) -> dict:
        if not DATA.exists():
            return {}
        try:
            return json.loads(DATA.read_text(encoding="utf-8")).get("projects", {})
        except Exception:
            return {}

    def _save(self) -> None:
        DATA.write_text(json.dumps({"updated": self._now(), "projects": self.projects}, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_active(self) -> str | None:
        if not ACTIVE.exists():
            return None
        try:
            return json.loads(ACTIVE.read_text(encoding="utf-8")).get("project_id")
        except Exception:
            return None

    def _save_active(self, pid: str | None) -> None:
        ACTIVE.write_text(json.dumps({"project_id": pid, "updated": self._now()}, indent=2, ensure_ascii=False), encoding="utf-8")

    def list(self) -> list:
        return sorted(self.projects.values(), key=lambda p: p.get("updated", ""), reverse=True)

    def get(self, pid: str) -> dict | None:
        return self.projects.get(pid)

    def create(self, name: str, description: str = "") -> dict:
        name = (name or "").strip() or "Projekt"
        pid = f"P-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        p = {"id": pid, "name": name, "description": description, "created": self._now(), "updated": self._now(), "files": [], "notes": ""}
        self.projects[pid] = p
        self._save()
        return {"success": True, "project": p}

    def update(self, pid: str, **fields) -> dict:
        p = self.projects.get(pid)
        if not p:
            return {"success": False, "error": "not_found"}
        for k in ("name", "description", "notes", "files"):
            if k in fields and fields[k] is not None:
                p[k] = fields[k]
        p["updated"] = self._now()
        self._save()
        return {"success": True, "project": p}

    def delete(self, pid: str) -> dict:
        if pid not in self.projects:
            return {"success": False, "error": "not_found"}
        del self.projects[pid]
        if self.active_id == pid:
            self.active_id = None
            self._save_active(None)
        self._save()
        return {"success": True}

    def set_active(self, pid: str | None) -> dict:
        if pid and pid not in self.projects:
            # try by name
            for p in self.projects.values():
                if p.get("name", "").lower() == (pid or "").lower():
                    pid = p["id"]
                    break
            else:
                return {"success": False, "error": "not_found", "reply": f"Projekt nicht gefunden: {pid}"}
        self.active_id = pid
        self._save_active(pid)
        name = self.projects[pid]["name"] if pid else "keins"
        return {"success": True, "project_id": pid, "reply": f"Aktives Projekt: {name}"}

    def open_by_name(self, name: str) -> dict:
        name = (name or "").strip()
        for p in self.projects.values():
            if p.get("name", "").lower() == name.lower() or name.lower() in p.get("name", "").lower():
                return self.set_active(p["id"])
        # auto-create
        created = self.create(name)
        self.set_active(created["project"]["id"])
        return {"success": True, "project": created["project"], "reply": f"Projekt angelegt und aktiv: {name}"}

    def context(self) -> str:
        if not self.active_id or self.active_id not in self.projects:
            return ""
        p = self.projects[self.active_id]
        return f"Aktives Projekt: {p.get('name')} — {p.get('description','')}"
