"""NEXUS + Hermes: gemeinsamer Skill-Bau ueber den Skill-Selbstbau-Weg (Eddie 27.10.: "die sollen was bauen zusammen").

Ablauf (nichts vorgetaeuscht, jeder Schritt mit Beleg):
 1. NEXUS-Core plant: Anforderungen -> registrierte READ_ONLY-Tasks (Capability-Mapping), Plan wird persistiert.
 2. Hermes fuehrt aus: schreibt die Skill-Definition mit seinem nativen write_file-Tool (hermes_agent.ask).
 3. NEXUS prueft das Hermes-Artefakt (JSON == Plan) und uebernimmt es ueber auto_apply_user_skill
    (Backup -> Registrierung -> Testlauf ueber Orchestrator/Bridge -> bei Fehler Rollback -> Audit).
 4. Aufruf per Chat: "mach einen <skill>" -> run_user_skill + deutsche Kurzfassung aus echten Task-Ergebnissen.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.common.paths import WORKSPACE

BUILD_RE = re.compile(r"\b(?:bau\w*|erstell\w*|leg\w*\s+an|anleg\w*|entwickl\w*)\b[\s\S]{0,80}\bskill\s*[\"'`„]?([a-z][a-z0-9_]{2,39})\b", re.I)
RUN_RE = re.compile(r"^\s*(?:mach|mache|fuehr\w*|führ\w*|starte?)\s+(?:mal\s+)?(?:einen|eine|ein|den|die|das)?\s*([a-z][a-z0-9_]{2,39})\s*(?:aus)?\s*[.!?]?\s*$", re.I)
FACETS = [
    (re.compile(r"\b(?:ram|arbeitsspeicher|speicher|platte|disk|laufwerk)\b", re.I), "system_status", "RAM + Speicher"),
    (re.compile(r"\bollama\b|\bmodell", re.I), "llm_status", "Ollama-Status"),
    (re.compile(r"\bhealth\b|\bgesundheit|nexus[- ]status", re.I), "nexus_status", "NEXUS-Health"),
]
HERMES_DIR = WORKSPACE / "hermes_tasks" / "skills"


def is_build(text: str) -> bool:
    return bool(BUILD_RE.search(text or ""))


def plan(text: str) -> dict:
    m = BUILD_RE.search(text or "")
    name = m.group(1).lower() if m else ""
    steps, covered = [], []
    for rx, action, label in FACETS:
        if rx.search(text or "") and action not in [s["action"] for s in steps]:
            steps.append({"action": action, "params": {}, "title": label})
            covered.append(label)
    return {"name": name, "steps": steps, "covered": covered, "planner": "nexus_core_capability_mapping",
            "created": datetime.now().isoformat(timespec="seconds")}


def _summary(text: str, plan_: dict, **kw) -> dict:
    return {"success": kw.get("ok", False), "intent": "hermes_work", "status": kw.get("status", "FAILED"),
            "reply": text, "error": kw.get("error"),
            "data": {"execution_status": kw.get("status", "FAILED"), "task_ids": kw.get("task_ids", []),
                     "provenance": "persisted_bridge_tasks", "order_type": "skill_build", "plan": plan_,
                     "hermes": kw.get("hermes"), "auto": kw.get("auto"), "tools": kw.get("tools", [])}}


def run(master: Any, text: str, session: dict | None = None) -> dict:
    from nexus.integrations import hermes_agent
    from nexus.skills import registry
    p = plan(text)
    name = p["name"]
    if not name or not p["steps"]:
        return _summary("BLOCKED: Kein Skillname oder keine pruefbaren Bausteine erkannt.", p, status="BLOCKED", error="SKILL_PLAN_EMPTY")
    HERMES_DIR.mkdir(parents=True, exist_ok=True)
    (registry.PENDING_DIR).mkdir(parents=True, exist_ok=True)
    (registry.PENDING_DIR / f"{name}.plan.json").write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    spec = {"name": name, "description": text.strip()[:300], "steps": p["steps"]}
    spec_txt = json.dumps(spec, ensure_ascii=False)
    task = (f"Nutze das Werkzeug write_file genau einmal: path='{name}.json', content = exakt dieser JSON-Text "
            f"(unveraendert, ohne Codeblock):\n{spec_txt}\nDanach antworte mit einem kurzen Satz.")
    h = hermes_agent.ask(task, workdir=str(HERMES_DIR))
    art = HERMES_DIR / f"{name}.json"
    hermes_info = {"request_id": h.get("request_id"), "model": h.get("model"), "status": (h.get("data") or {}).get("execution_status"),
                   "tool_calls": [{"tool": c.get("tool"), "ok": c.get("ok"), "ts": c.get("ts")} for c in ((h.get("data") or {}).get("tool_calls") or [])],
                   "artifact": str(art)}
    try:
        got = json.loads(art.read_text(encoding="utf-8")) if art.is_file() else None
    except Exception:
        got = None
    if not h.get("success") or not got or got.get("steps") != p["steps"]:
        return _summary("FAILED: Hermes hat die Skill-Definition nicht korrekt per Werkzeug geschrieben "
                        f"(Status {hermes_info['status']}, Datei {'vorhanden' if art.is_file() else 'fehlt'}).",
                        p, status="FAILED", error="HERMES_ARTIFACT_INVALID", hermes=hermes_info)
    existing = registry._load_user()
    if name in existing:
        return _summary(f"PASS: Skill '{name}' existiert bereits (keine Doppelanlage).", p, ok=True, status="PASS", hermes=hermes_info)
    rec = {"name": name, "description": got.get("description", ""), "steps": got["steps"], "test": None,
           "created": datetime.now().isoformat(timespec="seconds"), "state": "hermes_artifact", "hermes": hermes_info}
    auto = registry.auto_apply_user_skill(name, rec, {})
    post = registry._load_user().get(name, {}).get("last_run") or {}
    ids = _task_ids(post.get("order"))
    ok = bool(auto.get("applied"))
    lines = [("PASS" if ok else "FAILED") + f": Skill '{name}' gemeinsam gebaut.",
             f"NEXUS-Plan: {', '.join(p['covered'])} -> {', '.join(s['action'] for s in p['steps'])}",
             f"Hermes: {hermes_info['request_id']} write_file -> {art.name} ({'ok' if hermes_info['tool_calls'] else 'kein Tool'})",
             f"Uebernahme: {'registriert, Testlauf ' + str(auto.get('post_test')) if ok else 'Rollback (' + str(auto.get('reason')) + ')'}",
             f"Werkzeugbelege: {', '.join(ids) or '-'} (Work-Order {post.get('order')})"]
    return _summary("\n".join(lines), p, ok=ok, status="PASS" if ok else "FAILED", task_ids=ids, hermes=hermes_info, auto=auto,
                    error=None if ok else "SKILL_ROLLED_BACK")


def _task_ids(order_id: str | None) -> list[str]:
    if not order_id:
        return []
    p = WORKSPACE / "tasks" / "orchestrator" / f"{order_id}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return [s.get("task_id") for s in d.get("steps", []) if s.get("task_id")]
    except Exception:
        return []


def _task_result(tid: str) -> dict:
    try:
        return json.loads((WORKSPACE / "bridge" / "tasks" / f"{tid}.json").read_text(encoding="utf-8")).get("result") or {}
    except Exception:
        return {}


def summarize(steps: list[dict]) -> list[str]:
    out = []
    for s in steps:
        r = _task_result(s.get("task_id") or "")
        a = s.get("action")
        if a == "system_status":
            m = r.get("metrics") or {}
            ram = m.get("ram") or {}
            out.append(f"RAM: {ram.get('available_gb')} GB frei von {ram.get('total_gb')} GB ({ram.get('percent')} % belegt)")
            for d in m.get("disks") or []:
                mnt = str(d.get("mount") or "")[:2].upper()
                if mnt in ("C:", "G:") and d.get("total"):
                    out.append(f"{mnt} {round(d['free'] / 1024**3, 1)} GB frei von {round(d['total'] / 1024**3, 1)} GB")
        elif a == "llm_status":
            o = r.get("ollama") or {}
            out.append(f"Ollama: {'erreichbar' if o.get('reachable') else 'NICHT erreichbar'}, Modell {o.get('preferred')}, {len(o.get('models') or [])} Modelle installiert")
        elif a == "nexus_status":
            hh = r.get("health") or {}
            out.append(f"NEXUS-Health: {'OK' if hh.get('ok') else 'FEHLER'} (Score {hh.get('score')}, Port {hh.get('port')})")
    return out


def maybe_run(text: str) -> dict | None:
    from nexus.skills import registry
    m = RUN_RE.match(text or "")
    if not m:
        return None
    name = m.group(1).lower()
    if name not in registry._load_user():
        return None
    r = registry.run_user_skill(name, {})
    steps = r.get("steps") or []
    lines = summarize(steps) if r.get("success") else [f"Skill '{name}' fehlgeschlagen: {r.get('status') or r.get('error')}"]
    ids = [s.get("task_id") for s in steps if s.get("task_id")]
    return {"success": bool(r.get("success")), "intent": "skill_run", "status": "PASS" if r.get("success") else "FAILED",
            "reply": "\n".join(lines), "error": r.get("error"),
            "data": {"execution_status": "TOOLS_SUCCEEDED" if r.get("success") else "FAILED", "task_ids": ids,
                     "order": r.get("order"), "tools": [{"name": s.get("action"), "task_id": s.get("task_id")} for s in steps],
                     "provenance": "live_tool_result"}}
