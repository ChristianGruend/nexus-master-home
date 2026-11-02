"""Übergeordnete Projektaufträge: Ziel → geprüfter Ausführungsplan → vorhandene Bridge.

NEXUS bleibt die einzige Steuerung. Hermes leitet aus einem freigegebenen Projektziel die
benötigten Dateien, Inhalte und Werkzeugaufrufe ab; jede Ableitung wird vor der Ausführung
gegen die bestehenden Pfad-, Werkzeug- und Berechtigungsgrenzen geprüft (validate_task).
Ausführung und Nachweis laufen ausschließlich über HermesBrain und die NEXUS-Bridge.

Ein freies Projektziel ist keine pauschale Schreibfreigabe: es werden nur die bereits
freigegebenen Dateiwerkzeuge im freigegebenen Arbeitsbereich genutzt. Privilegierte
Aktionen (Shell, Geräte, Freigaben) bleiben außen vor und behalten ihre eigenen
Berechtigungen.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "nexus.hermes-project-order.v1"
BATCH_SIZE = 4  # HermesBrain-Auftragsvertrag: 1..4 Anforderungen je Lauf
MAX_PLAN_FILES = 8
STATE_DIR = "workspace/state"
STATE_PREFIX = "hermes-project-order-"
ACTION = "create_file → read_file → Inhaltvergleich"

PROJECT_MARKER = re.compile(
    r"\bprojekt\b|\bstatusdatei\b|\babschlussbericht\b|\bprojektplan\b|"
    r"plane\s+(?:die\s+)?(?:einzelnen\s+)?schritte|schritte\s+selbst|"
    r"mehrere\s+(?:dateien|schritte)",
    re.I,
)
DELIVERABLE = re.compile(r"arbeitsplan|statusdatei|abschlussbericht|projektplan|\.json|\.txt", re.I)
EXPLICIT_CONTENT = re.compile(
    r"(?:inhalt|content)\s*:?\s*(?:\"[\s\S]*?\"|„[\s\S]*?“|```(?:text)?\s*\n[\s\S]*?```)",
    re.I,
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def has_explicit_content(text: str) -> bool:
    """Eine angegebene Inhalt-Vorgabe ist eine explizite Dateioperation, kein Projektauftrag."""
    return bool(EXPLICIT_CONTENT.search(text or ""))


# Strukturierte inhaltliche Vorgabe: kein "Inhalt:"-Block, aber eine Anweisung, Punkte
# in einer neuen Datei festzuhalten/dokumentieren, plus sichtbare Aufzählungsstruktur.
_FIXED_OUTLINE = re.compile(
    r"\b(?:halte\s+(?:darin\s+)?fest|dokumentiere|protokolliere|erfasse\w*\s+(?:es\s+)?in\s+einer\s+datei|"
    r"list\w*\s+(?:darin|in\s+einer\s+datei)\s+(?:die\s+)?(?:folgenden\s+)?(?:punkte|inhalte|fakten))\b",
    re.I,
)
_LISTED = re.compile(r"(?:^|\n)\s*(?:[-•*]|\d{1,2}[.)])\s+", re.M)


def is_project_order(text: str, named_paths=()) -> bool:
    """Explizite Dateioperation und übergeordneter Projektauftrag werden getrennt.

    Projektauftrag liegt vor, wenn ein Schreibauftrag ohne festgelegten Inhalt entweder
    mehrere benannte Zieldateien nennt oder ein Projektziel mit übergeordneten Lieferobjekten
    beschreibt. Fragen, Beispiele und verneinte Schreibaufträge bleiben draußen.
    """
    low = (text or "").lower()
    if not re.search(r"\b(erstell\w*|schreib\w*|speicher\w*|create|write)\b", low):
        return False
    if has_explicit_content(text):
        return False
    if len({p.replace("\\", "/").lower() for p in (named_paths or []) if p}) >= 2:
        return True
    if PROJECT_MARKER.search(low):
        return True
    # Korrektur-/Dokumentationsaufträge mit strukturierter Punktvorgabe (kein blockweiser
    # "Inhalt:", aber eine Aufzählung, die der Planer in den Dateiinhalt übernehmen soll).
    # Das verhindert EXACT_CONTENT_REQUIRED im einfachen Dateipfad: ein reiner
    # Markdown-Befund ist kein Software-Implementierungsnachweis und wird auch so gemeldet.
    if _FIXED_OUTLINE.search(low) and _LISTED.search(text):
        return True
    return len(set(DELIVERABLE.findall(low))) >= 2


def _input_paths(text: str, root: Path) -> list[str]:
    """Bereits vorhandene Dateien, die das Ziel nur zum Auswerten *liest*.

    Ein Arbeitsplan kann vorhandene Eingabedateien nennen (Bestandsaufnahme,
    vorheriger Plan). Solche Pfade sind keine Erstell-Ziele; sie werden im
    freigegebenen Bereich gelesen und dem Planer als Nur-Lese-Kontext übergeben.
    """
    from nexus.master.evidence import _READ_CONTEXT, _create_target_paths, _read_path

    if not _READ_CONTEXT.search(text or ""):
        return []
    create_targets = {p.replace("\\", "/") for p in _create_target_paths(text)}
    found: list[str] = []
    for candidate in _read_path(text):
        path = candidate.replace("\\", "/")
        if path in create_targets:
            continue
        if "/" not in path and ":" not in path:
            path = "workspace/projects/hermes/" + path
        if path in found:
            continue
        if (root / path).is_file():
            found.append(path)
    return found


def _read_inputs(master, root: Path, text: str) -> list[dict]:
    """Vorhandene Eingabedateien über die geprüfte Bridge lesen; echte Belege sammeln."""
    import uuid
    from nexus.integrations.hermes_client import invoke, validate_task

    inputs: list[dict] = []
    for input_path in _input_paths(text, root):
        try:
            validate_task("read_file", {"path": input_path})
        except Exception:
            continue
        read = invoke({"operation": "submit", "task": "read_file", "params": {"path": input_path},
                       "correlation_id": str(uuid.uuid4())})
        fields = read.get("result") if isinstance(read.get("result"), dict) else {}
        if read.get("success") is True and isinstance(fields.get("content"), str):
            inputs.append({"path": input_path, "task_id": read.get("task_id"),
                           "content": fields.get("content"), "size": fields.get("size"),
                           "truncated": fields.get("truncated") is True})
    return inputs


def _state_path(root: Path, goal: str) -> Path:
    directory = root / STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:16]
    return directory / (STATE_PREFIX + digest + ".json")


def _load_state(path: Path):
    """None = noch kein Auftrag; list/dict = gespeicherter Auftrag; Fehlschlag wird nicht überschrieben."""
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return value if isinstance(value, dict) and value.get("schema") == SCHEMA else False


def _save_state(path: Path, state: dict) -> None:
    state["updated_at"] = _utc()
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _aggregate(state: dict) -> str:
    statuses = [b.get("status") for b in state.get("batches", [])]
    planned = math.ceil(len(state.get("requirements", [])) / BATCH_SIZE)
    if not statuses:
        return "PLANNED"
    if planned and len(statuses) >= planned and all(s == "PASS" for s in statuses):
        return "PASS"
    if any(s == "PASS" for s in statuses):
        return "PARTIAL"
    last = statuses[-1]
    return last if last in ("PARTIAL", "BLOCKED", "FAILED") else "BLOCKED"


def plan_steps(state: dict) -> list[dict]:
    """Ein geprüfter Arbeitsschritt je geplanter Datei, mit echten Werkzeugbelegen."""
    checks = {c.get("path"): c for b in state.get("batches", []) for c in b.get("verification", [])}
    owner = {r.get("path"): b for b in state.get("batches", []) for r in b.get("requirements", [])}
    steps = []
    for number, requirement in enumerate(state.get("requirements", []), 1):
        path = requirement.get("path")
        check = checks.get(path)
        batch = owner.get(path)
        if check:
            status = "PASS"
            ids = [check.get("create_task_id"), check.get("read_task_id")]
        elif batch:
            # Ohne geprüften Beleg keine ID zuweisen: Auftragsbelege stehen unter data.batches.
            status = str(batch.get("status") or "PLANNED")
            ids = []
        else:
            status = "PLANNED"
            ids = []
        steps.append({
            "id": number,
            "path": path,
            "action": ACTION,
            "status": status,
            "task_ids": [i for i in ids if i],
        })
    return steps


def _attach_inputs(out: dict, inputs: list) -> None:
    """Gelesene Eingabedateien als echte Belege an ein Ergebnis hängen (inkl. Task-IDs)."""
    if not inputs or not isinstance(out.get("data"), dict):
        return
    data = out["data"]
    data["inputs"] = [{"path": i.get("path"), "task_id": i.get("task_id")} for i in inputs]
    read_ids = [i.get("task_id") for i in inputs if i.get("task_id")]
    seen = list(data.get("task_ids") or [])
    data["task_ids"] = read_ids + [t for t in seen if t not in read_ids]
    listing = ", ".join(f"{i.get('path')} (Task-ID {i.get('task_id')})" for i in inputs if i.get("task_id"))
    if listing:
        out["reply"] = (str(out.get("reply") or "") + "\nGelesene Eingabedateien: " + listing)


def _render(state: dict) -> dict:
    from nexus.master.evidence import _response

    inputs = [i for i in (state.get("inputs") or []) if isinstance(i, dict)]
    input_ids = [i.get("task_id") for i in inputs if i.get("task_id")]
    requirements = [r for b in state.get("batches", []) for r in b.get("requirements", [])]
    task_ids = [t for b in state.get("batches", []) for t in b.get("task_ids", [])]
    verification = [c for b in state.get("batches", []) for c in b.get("verification", [])]
    error = next((b.get("error") for b in state.get("batches", []) if b.get("error")), None)
    out = _response({
        "status": state.get("status") or "BLOCKED",
        "task_ids": input_ids + [t for t in task_ids if t not in input_ids],
        "requirements": requirements,
        "verification": verification,
        "error": error,
        "model": state.get("model"),
        "run_id": state.get("plan_id"),
    })
    steps = plan_steps(state)
    data = out.get("data") if isinstance(out.get("data"), dict) else {}
    data.update(
        order_type="project_order",
        plan=steps,
        plan_id=state.get("plan_id"),
        plan_status=state.get("status"),
        goal=state.get("goal"),
        inputs=[{"path": i.get("path"), "task_id": i.get("task_id")} for i in inputs],
        batches=[
            {"index": b.get("index"), "paths": b.get("paths"), "status": b.get("status"),
             "task_ids": b.get("task_ids", []), "error": b.get("error"), "journal": b.get("journal")}
            for b in state.get("batches", [])
        ],
        provenance=data.get("provenance") or "persisted_bridge_tasks",
    )
    out["data"] = data
    out["plan"] = steps
    header = ["AUSFÜHRUNGSPLAN (Projektauftrag " + str(state.get("plan_id")) + ", "
              + str(len(steps)) + " Schritte):"]
    for step in steps:
        line = "{id}. {path} – {action} – {status}".format(**step)
        if step["task_ids"]:
            line += " – Task-IDs: " + ", ".join(step["task_ids"])
        header.append(line)
    for item in inputs:
        header.append("Eingabe gelesen: " + str(item.get("path"))
                      + (" (Task-ID " + str(item.get("task_id")) + ")" if item.get("task_id") else ""))
    header.append("")
    out["reply"] = "\n".join(header) + "\n" + str(out.get("reply") or "")
    return out


def run(master, goal: str, session: dict, required_paths=None) -> dict:
    """Projektauftrag planen, jeden Schritt prüfen, in Batches über die Bridge ausführen."""
    from nexus.integrations.hermes_brain import HermesBrain
    from nexus.master.evidence import blocked

    text = str(goal or "").strip()
    if not text or len(text) > 6000:
        return blocked("PROJECT_GOAL_REQUIRED", "Für diesen Projektauftrag fehlt ein eindeutiges Ziel.")

    root = Path(str(getattr(getattr(master, "project_fs", None), "root", "") or "."))
    path = _state_path(root, text)
    state = _load_state(path)
    if state is False:
        return blocked("PROJECT_ORDER_STATE_UNREADABLE",
                       "Der gespeicherte Fortschritt dieses Projektauftrags ist nicht lesbar. "
                       "Es wird nichts neu abgeleitet und nichts geschrieben.")
    if state is None:
        inputs = _read_inputs(master, root, text)
        derived = HermesBrain(master).derive_plan(text, required_paths=required_paths,
                                                  max_files=MAX_PLAN_FILES, context=inputs)
        if not derived.get("success"):
            code = str(derived.get("error") or "PLAN_NOT_DERIVED")
            if code == "UNSUPPORTED_ACTIONS":
                reasons = [str(r) for r in (derived.get("details") or []) if r]
                detail = ("Dieser Projektauftrag verlangt nicht unterstützte Aktionen: "
                          + "; ".join(reasons)
                          + ". Der freigegebene Bereich erlaubt nur neue Textdateien (.txt/.md/.json) direkt "
                            "unter workspace/projects/hermes/. Es wurde keine Datei geschrieben und kein "
                            "Programm ausgeführt.")
                return blocked("UNSUPPORTED_PROJECT_ACTION", detail)
            out = blocked("PLAN_NOT_DERIVED:" + code,
                          "Der Projektauftrag konnte nicht in einen geprüften Arbeitsplan übersetzt "
                          "werden (" + code + "). Es wurde keine Datei geschrieben.")
            if isinstance(out.get("data"), dict):
                out["data"]["plan_error"] = code
                _attach_inputs(out, inputs)
            return out
        state = {
            "schema": SCHEMA,
            "goal": text,
            "plan_id": "PLAN-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
            "created_at": _utc(),
            "model": derived.get("model"),
            "inputs": [{"path": i["path"], "task_id": i["task_id"], "size": i.get("size"),
                        "truncated": i.get("truncated")} for i in inputs],
            "requirements": derived["requirements"],
            "batches": [],
            "status": "PLANNED",
        }
        # Plan dauerhaft festhalten: spätere Läufe leiten nicht erneut ab und schreiben nicht doppelt.
        _save_state(path, state)

    session["hermes_order"] = {"task": text, "requirements": state["requirements"], "project": True}
    persist = getattr(master, "_persist_chat_order", None)
    if persist:
        try:
            persist()
        except Exception:
            pass

    requirements = state["requirements"]
    for index in range(0, len(requirements), BATCH_SIZE):
        batch_index = index // BATCH_SIZE
        batch = requirements[index:index + BATCH_SIZE]
        existing = state["batches"][batch_index] if batch_index < len(state["batches"]) else None
        if existing and existing.get("status") == "PASS":
            continue  # belegter Abschluss steht; kein zweiter Schreibversuch.
        # Kein Modell im Schreibpfad: der geprüfte Planinhalt wird bytegenau geschrieben und
        # zurückgelesen. Ein Modell, das exakte Inhalte in Tool-Argumente abschreibt, verdoppelte
        # nachweislich Escapes ("\n" -> "\\n") und scheiterte an TOOL_OUTSIDE_AUTHORIZED_CONTRACT.
        result = HermesBrain(master).apply_requirements(batch)
        record = {
            "index": batch_index,
            "paths": [r["path"] for r in batch],
            "requirements": batch,
            "status": result.get("status") or "BLOCKED",
            "task_ids": list(result.get("task_ids") or []),
            "verification": list(result.get("verification") or []),
            "journal": result.get("journal"),
            "error": result.get("error"),
            "finished_at": _utc(),
        }
        if batch_index < len(state["batches"]):
            state["batches"][batch_index] = record
        else:
            state["batches"].append(record)
        state["status"] = _aggregate(state)
        _save_state(path, state)
        if record["status"] != "PASS":
            break

    state["status"] = _aggregate(state)
    _save_state(path, state)
    return _render(state)
