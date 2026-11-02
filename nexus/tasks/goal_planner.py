"""008-P04: Ziel-Planer. Ziel -> LLM-Plan (JSON) -> Orchestrator.

Ablauf: plan() erzeugt Schritte nur aus einer SAFE-Allowlist; Validierung und
Ausfuehrung laufen ueber den bestehenden Orchestrator (PRIVILEGED abgelehnt,
Pfad-Preflight, eine deklarierte Reparatur je Schritt, persistente
Wiederaufnahme). Selbstkorrektur: schlaegt der Auftrag fehl, wird genau EIN
Neuplan mit der echten Diagnose erstellt (kein Endlos-Loop).
"""
from __future__ import annotations

import json
import re
from typing import Any

PLANNER_ACTIONS = {
    "create_dir": "Ordner anlegen {path}",
    "create_file": "neue Datei {path, content}",
    "write_file": "Datei ueberschreiben mit Backup {path, content}",
    "read_file": "Datei lesen {path}",
    "list_files": "Ordner auflisten {path}",
    "copy_file": "Datei kopieren {src, dst}",
    "search_files": "Dateien suchen {query, path?}",
    "code_analyze": "Python-Datei analysieren {path}",
    "system_diagnose": "Systemdiagnose Platten/RAM/Ports {}",
    "git_status": "Git-Status {}",
    "llm_status": "KI-Status {}",
}
MAX_STEPS = 8


def _llm(messages: list[dict], model: str | None = None) -> str:
    """Direkter lokaler Ollama-Aufruf: JSON-Format, ohne Denkphase (schneller, nie leer)."""
    import urllib.request
    from nexus.common import ai_layer
    from nexus.common.ollama_client import OllamaClient
    c = OllamaClient()
    models = [m for m in dict.fromkeys([model or ai_layer.resolve_role_model("strong", c), ai_layer.resolve_role_model("fast", c)]) if m]
    last = "no_model"
    for i, use in enumerate(models):
        if i:
            # 008-ROUTE: vor dem Fallback das vorige Modell entladen -> nie zwei Modelle gleichzeitig im RAM.
            try:
                urllib.request.urlopen(urllib.request.Request(ai_layer.ollama_base_url().rstrip("/") + "/api/generate",
                    data=json.dumps({"model": models[i - 1], "keep_alive": 0}).encode(),
                    headers={"Content-Type": "application/json"}), timeout=15).read()
            except Exception:
                pass
        body = {"model": use, "messages": messages, "stream": False, "format": "json", "think": False,
                # 008-ROUTE: Modell nach dem Aufruf entladen (AI-Layer-Policy, Standard 0) -> nie zwei Modelle resident.
                "keep_alive": ai_layer.keep_alive_value(),
                "options": {"temperature": 0.1, "num_predict": 1500, "num_ctx": ai_layer.unified_num_ctx()}}
        try:
            req = urllib.request.Request(ai_layer.ollama_base_url().rstrip("/") + "/api/chat", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=240) as r:
                txt = (json.loads(r.read()).get("message") or {}).get("content") or ""
            if txt.strip():
                return txt
            last = f"empty reply from {use}"
        except Exception as exc:
            last = f"{use}: {exc}"
    raise RuntimeError(f"LLM_UNAVAILABLE: {last}")


def _parse(text: str) -> list[dict[str, Any]]:
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    data = json.loads(m.group(0) if m else text)
    steps = data.get("steps") if isinstance(data, dict) else data
    if not isinstance(steps, list) or not steps:
        raise ValueError("PLAN_EMPTY")
    out = []
    for i, s in enumerate(steps[:MAX_STEPS]):
        a = str(s.get("action") or "").strip()
        if a not in PLANNER_ACTIONS:
            raise ValueError(f"PLAN_ACTION_NOT_ALLOWED: {a}")
        st = {"action": a, "params": dict(s.get("params") or {}), "title": str(s.get("title") or a)[:120]}
        if i:
            st["depends_on"] = [i - 1]
        out.append(st)
    return out


def _prompt(goal: str, feedback: str = "") -> list[dict]:
    cat = "\n".join(f"- {k}: {v}" for k, v in PLANNER_ACTIONS.items())
    sys = ("Du bist der NEXUS-Planer. Zerlege das Ziel in hoechstens 8 Schritte. Nutze NUR diese Aktionen:\n" + cat +
           "\nPfade relativ zum Projekt, z.B. workspace/... . Antworte nur als JSON: "
           '{"steps":[{"action":"...","params":{...},"title":"..."}]}')
    msgs = [{"role": "system", "content": sys}, {"role": "user", "content": "Ziel: " + goal}]
    if feedback:
        msgs.append({"role": "user", "content": "Der vorige Plan ist gescheitert: " + feedback +
                     "\nErstelle einen korrigierten Plan fuer die RESTLICHE Arbeit (bereits erledigte Schritte nicht wiederholen). "
                     "Wiederhole NICHT denselben fehlgeschlagenen Schritt. Bei 'path not found' zuerst mit search_files oder list_files "
                     "den echten Pfad ermitteln und dann diesen verwenden. Erfinde keine Inhalte."})
    return msgs


def _observe(task_id: str | None, limit: int = 700) -> str:
    """Echtes Task-Ergebnis (Bridge-Record) als Beobachtung fuer den Neuplan."""
    if not task_id:
        return "kein Ergebnis"
    try:
        from nexus.common.paths import WORKSPACE
        rec = json.loads((WORKSPACE / "bridge" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
        return json.dumps(rec.get("result"), ensure_ascii=False)[:limit]
    except Exception:
        return "Ergebnis nicht lesbar"


def run_goal(goal: str, *, dry_run: bool = False, model: str | None = None) -> dict[str, Any]:
    from nexus.tasks.orchestrator import Orchestrator
    goal = str(goal or "").strip()
    if not goal:
        return {"success": False, "error": "GOAL_REQUIRED"}
    o = Orchestrator()
    attempts: list[dict[str, Any]] = []
    feedback = ""
    for attempt in (1, 2, 3):  # Erstplan + max. zwei beobachtungsgestuetzte Neuplaene
        try:
            steps = _parse(_llm(_prompt(goal, feedback), model))
            sig = [(x["action"], x["params"]) for x in steps]
            if any([(x["action"], x["params"]) for x in a.get("steps", [])] == sig for a in attempts):
                attempts.append({"attempt": attempt, "status": "replan_identical", "error": "Neuplan identisch mit gescheitertem Plan"})
                break
            if dry_run:
                return {"success": True, "dry_run": True, "plan": steps}
            wo = o.create(f"goal: {goal[:80]}", steps)
            wo = o.run(wo.id)
        except Exception as exc:
            attempts.append({"attempt": attempt, "status": "plan_error", "error": str(exc)})
            feedback = str(exc)
            continue
        info = {"attempt": attempt, "order": wo.id, "status": wo.status, "result": wo.result,
                "steps": [{"action": s.action, "params": s.params, "status": s.status, "task_id": s.task_id, "diagnosis": s.diagnosis} for s in wo.steps]}
        attempts.append(info)
        if wo.status == "succeeded" and attempt > 1 and wo.steps[-1].action in ("search_files", "list_files") and attempt < 3:
            # Neuplan endete mit reiner Erkundung: Beobachtung einspeisen und Ziel zu Ende fuehren
            feedback = ("Erkundung abgeschlossen, das Ziel ist aber noch nicht erfuellt. Echte Ergebnisse: " +
                        str([f"{s.action} {s.params} -> {_observe(s.task_id)}" for s in wo.steps]))
            continue
        if wo.status == "succeeded":
            return {"success": True, "goal": goal, "self_corrected": attempt > 1, "attempts": attempts}
        done = [f"{s.action} {s.params} -> {_observe(s.task_id)}" for s in wo.steps if s.status == "succeeded"]
        feedback = f"{wo.result}. Erledigt mit echten Ergebnissen: {done}"
    return {"success": False, "goal": goal, "error": "GOAL_FAILED_AFTER_REPLAN", "attempts": attempts}


def resume_open(limit: int = 20) -> dict[str, Any]:
    """Nach Core-Neustart: unterbrochene Auftraege (planned/running mit offenem Cursor) fortsetzen."""
    from nexus.tasks.orchestrator import Orchestrator, STORE
    o = Orchestrator()
    out = []
    for p in sorted(STORE.glob("WO-*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        w = o.get(p.stem)
        if w and w.status in ("running", "planned"):
            w2 = o.resume(w.id)
            out.append({"order": w.id, "before": w.status, "after": w2.status, "result": w2.result})
    return {"success": True, "resumed": out}


# --- Phase 2 Schritt 6: statischer Plan (aus master/planner.py) ---
def static_plan(intent: str, message: str = "") -> list[dict]:
    """Phase 2: statischer Fallback-Plan (frueher master.planner.plan), nur Anzeige."""
    steps = []
    if intent == "research":
        steps = [
            {"id": 1, "action": "search", "detail": "Web-Suche ausführen"},
            {"id": 2, "action": "summarize", "detail": "Ergebnisse zusammenfassen"},
        ]
    elif intent == "youtube_search":
        steps = [
            {"id": 1, "action": "youtube_search", "detail": "YouTube Live-Suche (Titel+Watch-URL)"},
            {"id": 2, "action": "report", "detail": "Nur echte Treffer melden oder BLOCKED"},
        ]
    elif intent == "code":
        steps = [
            {"id": 1, "action": "extract", "detail": "Code extrahieren"},
            {"id": 2, "action": "run", "detail": "In Sandbox ausführen"},
            {"id": 3, "action": "report", "detail": "Ergebnis melden"},
        ]
    elif intent == "task":
        steps = [
            {"id": 1, "action": "analyze", "detail": "Anforderung verstehen"},
            {"id": 2, "action": "create_task", "detail": "Task anlegen"},
            {"id": 3, "action": "execute_safe", "detail": "Sichere Schritte lokal ausführen"},
        ]
    elif intent == "file":
        steps = [
            {"id": 1, "action": "resolve_path", "detail": "Pfad im Sandbox klären"},
            {"id": 2, "action": "io", "detail": "Lesen/Schreiben"},
        ]
    elif intent == "self_improve":
        steps = [
            {"id": 1, "action": "scan", "detail": "Korrekturen, Smokes, Logs scannen"},
            {"id": 2, "action": "queue", "detail": "SELF_IMPROVE_QUEUE.md schreiben"},
            {"id": 3, "action": "apply_safe", "detail": "Optional safe applies / Report"},
        ]
    elif intent in ("build", "run_script", "shell"):
        steps = [
            {"id": 1, "action": "resolve", "detail": "Skript/Befehl unter workspace/scripts oder Shell klären"},
            {"id": 2, "action": "execute", "detail": "Echt ausführen (run_script/shell, langes Timeout)"},
            {"id": 3, "action": "report", "detail": "Ergebnis und Start-Hinweise melden"},
        ]
    else:
        steps = [{"id": 1, "action": "llm", "detail": "Antwort mit Kontext erzeugen"}]
    return steps
