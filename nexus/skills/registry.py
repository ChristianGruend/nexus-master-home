"""Core skills registry — thin view over master KNOWN_TOOLS + Core capabilities.
Phase 3: discovery only (no new execution engine). Local-first.
"""
from __future__ import annotations

from typing import Any

from nexus.common.response import envelope

# Built-in Core skill descriptors (stable APIs already wired)
_CORE_SKILLS: list[dict[str, Any]] = [
    {"id": "chat_local", "name": "Chat lokal", "area": "CHAT", "via": "POST /api/chat", "model": "qwen3.5:9b"},
    {"id": "files_sandbox", "name": "Dateien Sandbox", "area": "DATEIEN", "via": "GET/POST /api/files*"},
    {"id": "memory", "name": "Memory", "area": "MEMORY", "via": "GET/POST /api/memory*"},
    {"id": "system_status", "name": "System Status", "area": "SYSTEM", "via": "GET /api/system/metrics|/api/status"},
    {"id": "voice_tts_stt", "name": "Voice Basics", "area": "VOICE", "via": "POST /api/voice/tts|stt|turn"},
    {"id": "apps_open", "name": "Apps Open", "area": "APPS", "via": "POST /api/apps/open", "notes": "youtube/whatsapp wired"},
]


def list_skills() -> dict[str, Any]:
    tools: list[str] = []
    try:
        from nexus.master.tools import KNOWN_TOOLS
        tools = sorted(KNOWN_TOOLS)
    except Exception as e:
        return envelope(False, error="tools_unavailable", details={"reason": str(e)}, skills=[], tools=[])
    skills = list(_CORE_SKILLS)
    for name in tools:
        skills.append({
            "id": f"tool:{name}",
            "name": name,
            "area": "SKILLS",
            "via": "master/tools KNOWN_TOOLS",
            "kind": "tool",
        })
    skills = [{**s, "registered": True, "reachable": None, "tested": None, "ready": None, "provenance": "registry_only"} for s in skills]
    try:
        skills.extend(list_user_skills())
    except Exception:
        pass
    return envelope(True, skills=skills, tools=tools, count=len(skills), core_count=len(_CORE_SKILLS), tool_count=len(tools))


# --- 008-P10: selbst erstellte Skills (kontrollierte Weiterentwicklung) ---
# Ein User-Skill ist eine benannte, persistierte Folge registrierter SAFE-Tasks.
# Kein freier Code: Validierung + Ausfuehrung laufen ueber den Orchestrator
# (PRIVILEGED abgelehnt, Pfad-Preflight, Selbstkorrektur, Wiederaufnahme).
import json as _json
import re as _re
from datetime import datetime as _dt
from pathlib import Path as _Path

USER_SKILLS = _Path(__file__).resolve().parents[2] / "workspace" / "skills" / "user_skills.json"
_NAME_RE = _re.compile(r"^[a-z0-9_]{3,40}$")


def _load_user() -> dict[str, Any]:
    if USER_SKILLS.is_file():
        return _json.loads(USER_SKILLS.read_text(encoding="utf-8"))
    return {}


def _fill(obj: Any, params: dict[str, Any]) -> Any:
    if isinstance(obj, str):
        for k, v in params.items():
            obj = obj.replace("{" + k + "}", str(v))
        return obj
    if isinstance(obj, dict):
        return {k: _fill(v, params) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, params) for v in obj]
    return obj


def create_user_skill(name: str, steps: list[dict[str, Any]], description: str = "", author: str = "nexus") -> dict[str, Any]:
    from nexus.tasks.orchestrator import Orchestrator, STORE  # noqa: F401
    from nexus.tasks.orchestrator import TASK_PERMISSIONS, PermissionLevel
    name = str(name or "").strip().lower()
    if not _NAME_RE.match(name):
        return envelope(False, error="invalid_skill_name", details={"rule": "a-z0-9_, 3-40"})
    if not steps:
        return envelope(False, error="steps_required")
    for s in steps:
        a = str(s.get("action") or "")
        if a not in TASK_PERMISSIONS:
            return envelope(False, error="unknown_action", details={"action": a})
        if TASK_PERMISSIONS[a] == PermissionLevel.PRIVILEGED:
            return envelope(False, error="privileged_not_allowed_in_skill", details={"action": a})
    data = _load_user()
    if name in data or any(s["id"] == name for s in _CORE_SKILLS):
        return envelope(False, error="skill_exists", details={"name": name})
    data[name] = {"description": str(description), "steps": steps, "author": author, "created": _dt.now().isoformat(timespec="seconds"), "runs": 0}
    USER_SKILLS.parent.mkdir(parents=True, exist_ok=True)
    tmp = USER_SKILLS.with_suffix(".tmp")
    tmp.write_text(_json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(USER_SKILLS)
    return envelope(True, skill=name, steps=len(steps))


def run_user_skill(name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    from nexus.tasks.orchestrator import Orchestrator
    data = _load_user()
    sk = data.get(str(name or "").strip().lower())
    if not sk:
        return envelope(False, error="skill_not_found", details={"name": name})
    try:
        o = Orchestrator()
        wo = o.create(f"skill:{name}", _fill(sk["steps"], dict(params or {})))
        wo = o.run(wo.id)
    except ValueError as exc:
        return envelope(False, error="skill_invalid", details={"reason": str(exc)})
    sk["runs"] = int(sk.get("runs", 0)) + 1
    sk["last_run"] = {"order": wo.id, "status": wo.status, "at": _dt.now().isoformat(timespec="seconds")}
    tmp = USER_SKILLS.with_suffix(".tmp")
    tmp.write_text(_json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(USER_SKILLS)
    return envelope(wo.status == "succeeded", order=wo.id, status=wo.status, result=wo.result,
                    steps=[{"action": s.action, "status": s.status, "task_id": s.task_id, "diagnosis": s.diagnosis} for s in wo.steps])


def list_user_skills() -> list[dict[str, Any]]:
    return [{"id": f"user:{k}", "name": k, "area": "USER_SKILL", "description": v.get("description", ""),
             "steps": len(v.get("steps", [])), "runs": v.get("runs", 0), "registered": True,
             "reachable": None, "tested": None, "ready": None,
             "provenance": "user_skills.json"} for k, v in _load_user().items()]


# --- 008-P10: Skill-Selbstbau-Pipeline: Entwurf (LLM) -> Probelauf -> Freigabe -> Registrierung ---
PENDING_DIR = USER_SKILLS.parent / "pending"


def draft_user_skill(name: str, description: str, test_params: dict[str, Any] | None = None) -> dict[str, Any]:
    from nexus.tasks import goal_planner as gp
    name = str(name or "").strip().lower()
    if not _NAME_RE.match(name):
        return envelope(False, error="invalid_skill_name")
    prompt = (str(description) + "\nErzeuge einen wiederverwendbaren Skill. Verwende Platzhalter in geschweiften Klammern "
              "fuer Eingaben: " + ", ".join("{" + k + "}" for k in (test_params or {})))
    try:
        steps = gp._parse(gp._llm(gp._prompt(prompt)))
    except Exception as exc:
        return envelope(False, error="draft_failed", details={"reason": str(exc)})
    from nexus.tasks.orchestrator import Orchestrator
    test = None
    if test_params is not None:
        o = Orchestrator()
        try:
            wo = o.run(o.create(f"skill-test:{name}", _fill(steps, dict(test_params))).id)
            test = {"order": wo.id, "status": wo.status, "result": wo.result}
        except ValueError as exc:
            test = {"status": "invalid", "result": str(exc)}
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"name": name, "description": str(description), "steps": steps, "test": test,
           "created": _dt.now().isoformat(timespec="seconds"), "state": "awaiting_approval"}
    (PENDING_DIR / f"{name}.json").write_text(_json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    if (test or {}).get("status") == "succeeded":
        # 008-AUTO (Eddie 08:38): autonome Uebernahme ohne Freigabe, mit Sicherheitsnetz.
        auto = auto_apply_user_skill(name, rec, dict(test_params or {}))
        return envelope(auto["applied"], skill=name, steps=steps, test=test, auto=auto)
    return envelope(True, pending=name, steps=steps, test=test, next="Probelauf nicht bestanden - keine Uebernahme")


SELFBUILD_AUDIT = USER_SKILLS.parent.parent / "audit" / "selfbuild_008.jsonl"


def _audit_selfbuild(entry: dict[str, Any]) -> None:
    try:
        SELFBUILD_AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with SELFBUILD_AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps({"at": _dt.now().isoformat(timespec="seconds"), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def auto_apply_user_skill(name: str, rec: dict[str, Any], test_params: dict[str, Any]) -> dict[str, Any]:
    """Backup -> Registrierung -> Nachtest -> bei Fehler automatisches Rollback. Jede Uebernahme wird auditiert."""
    import shutil
    backup = USER_SKILLS.with_name(USER_SKILLS.name + f".bak_auto_{_dt.now():%Y%m%d_%H%M%S}")
    existed = USER_SKILLS.is_file()
    if existed:
        shutil.copyfile(USER_SKILLS, backup)
    r = create_user_skill(name, rec["steps"], rec.get("description", ""), author="nexus-selfbuild")
    post = run_user_skill(name, test_params) if r.get("success") else r
    if r.get("success") and post.get("success"):
        rec["state"] = "registered_auto"
        (PENDING_DIR / f"{name}.json").write_text(_json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        out = {"applied": True, "rolled_back": False, "backup": str(backup) if existed else None, "post_test": post.get("status")}
    else:
        if existed:
            shutil.copyfile(backup, USER_SKILLS)
        elif USER_SKILLS.is_file():
            data = _load_user(); data.pop(name, None)
            USER_SKILLS.write_text(_json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        rec["state"] = "rolled_back"
        (PENDING_DIR / f"{name}.json").write_text(_json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        out = {"applied": False, "rolled_back": True, "reason": post.get("error") or post.get("status")}
    _audit_selfbuild({"skill": name, **out})
    return out


def approve_user_skill(name: str, approve: bool) -> dict[str, Any]:
    name = str(name or "").strip().lower()
    pf = PENDING_DIR / f"{name}.json"
    if not pf.is_file():
        return envelope(False, error="pending_not_found")
    rec = _json.loads(pf.read_text(encoding="utf-8"))
    if not approve:
        rec["state"] = "rejected"; pf.write_text(_json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return envelope(True, skill=name, state="rejected")
    if (rec.get("test") or {}).get("status") != "succeeded":
        return envelope(False, error="test_not_passed", details={"test": rec.get("test")})
    r = create_user_skill(name, rec["steps"], rec.get("description", ""), author="nexus-selfbuild")
    if r.get("success"):
        rec["state"] = "registered"; pf.write_text(_json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return r
