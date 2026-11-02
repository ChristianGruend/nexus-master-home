"""NEXUS entwickelt sich selbst weiter – nur als geprüfter, freigabepflichtiger Patch.

Ablauf (jeder Schritt mit Bridge-Beleg, Fortschritt in workspace/state/hermes-nexus-order-*.json):
  1. Hermes wählt 1–2 betroffene NEXUS-Dateien (nur freigegebene Pfade, ≤ 40 KB).
  2. Hermes liefert SEARCH/REPLACE-Änderungen; NEXUS wendet sie deterministisch an.
  3. hermes_prepare_nexus_patch  – getrennter Vorschlag mit Original, Diff, Manifest.
  4. hermes_test_nexus_patch     – Kompilieren, Import, betroffene Tests im AppContainer
                                   gegen die Ausgangslage. Bei Regression: echte Testausgabe
                                   zurück an Hermes, begrenzt auf MAX_FIX_ROUNDS.
  5. nexus_apply_patch           – PRIVILEGED: Freigabe über /approvals. Nach "weiter":
                                   anwenden, Sicherung, Hash-/Syntaxprüfung, Rückweg.
  6. Python-Änderungen werden erst nach nexus_restart (ebenfalls PRIVILEGED) aktiv.

Nie: Eingriffe in Authentifizierung, Freigabeprozess, Berechtigungen, Sandbox oder den
Werkzeugvertrag (fest gesperrt in dev_executors.PROTECTED), keine Selbständerung ohne Freigabe.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from nexus.integrations.hermes_brain import hermes_lessons_block

SCHEMA = "nexus.hermes-nexus-order.v1"
STATE_PREFIX = "hermes-nexus-order-"
MAX_FILE_BYTES = 40_000
MAX_FIX_ROUNDS = 2
MAX_MODEL_CALLS = 8

SELF_REF = re.compile(
    r"\b(?:f(?:ü|ue)r|in|an|im)\s+(?:das\s+|die\s+|den\s+)?nexus\b|\bnexus[-\s]?(?:oberfl(?:ä|ae)che|ui|funktion\w*|"
    r"code|quellcode|chamber|master\s+home|dashboard)\b|\beure?n?\s+(?:eigene[nm]?\s+)?(?:benutzer)?(?:oberfl(?:ä|ae)che|"
    r"code|quellcode|funktion\w*|ui)\b|\beure?n?\s+eigenen\s+code\b", re.I)
CHANGE_VERB = re.compile(r"\b(?:bau|erstell|entwick|implementier|erweiter|verbesser|behebt?|beheb\w*|fix\w*|"
                         r"änder|aender|ergänz|ergaenz|integrier|schreib|prüf|pruef|repar\w*)\w*", re.I)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_LOADED_AT = _utc()


# t329u: Nur "weiche" Verben (erstellen/schreiben/pruefen/verifizieren) mit einem
# Plan-/Bericht-/Statusobjekt sind Lese- bzw. Planungsauftraege, kein Selbstbau.
STRONG_CHANGE = re.compile(r"\b(?:bau|entwick|implementier|erweiter|verbesser|beheb|behebt|fix|\u00e4nder|aender|"
                           r"erg\u00e4nz|ergaenz|integrier|repar|patch|umbau|entfern)\w*", re.I)
PLAN_ONLY = re.compile(r"\b(?:\w*plan\w*|planung|schritte|bericht\w*|zusammenfassung|status\w*|"
                       r"verifizier\w*|testauftrag|checkliste|analyse)\b", re.I)


def _plan_only(sentence: str) -> bool:
    return bool(PLAN_ONLY.search(sentence)) and not STRONG_CHANGE.search(sentence)


def is_nexus_change(text: str) -> bool:
    from nexus.master.code_order import write_negated
    low = str(text or "").lower()
    if write_negated(low) or low.rstrip().endswith("?") or re.match(r"\s*(?:wie|was|warum|wieso|kann)\b", low):
        return False
    # Selbstbezug und Änderungsverb müssen im selben Satz stehen; bedingte oder verneinte
    # Erwähnungen ("Falls das System ... nicht in NEXUS integrieren darf") zählen nicht.
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", low):
        ref = SELF_REF.search(sentence)
        if ref and CHANGE_VERB.search(sentence) and _plan_only(sentence):
            continue  # t329u: Plan/Pruef/Bericht-Auftrag UEBER NEXUS ist keine Code-Aenderung
        if ref and CHANGE_VERB.search(sentence) and not re.search(
                r"\b(?:falls|wenn|sofern|ob|nicht|kein\w*|ohne|nie|niemals)\b", sentence):
            return True
    return False


def _state_path(root: Path, goal: str) -> Path:
    d = root / "workspace" / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / (STATE_PREFIX + hashlib.sha256(goal.encode("utf-8")).hexdigest()[:16] + ".json")


def _save(path: Path, state: dict) -> None:
    from nexus.common.atomic import atomic_write_json
    state["updated_at"] = _utc()
    atomic_write_json(path, state)


def _candidates(root: Path) -> list[dict]:
    from nexus.bridge.dev_executors import nexus_target_allowed
    out = []
    for p in sorted((root / "nexus").rglob("*")):
        rel = p.relative_to(root).as_posix()
        if (p.is_file() and p.suffix in (".py", ".html", ".css", ".js") and "vendor" not in p.parts
                and nexus_target_allowed(rel) and p.stat().st_size <= MAX_FILE_BYTES):
            out.append({"path": rel, "bytes": p.stat().st_size})
    return out


def _model(master, state, messages, *, num_predict=3000, json_mode=False) -> dict:
    if state["model_calls"] >= MAX_MODEL_CALLS:
        return {"success": False, "error": "MODEL_CALL_LIMIT"}
    state["model_calls"] += 1
    kwargs = {"temperature": 0.1, "num_predict": num_predict}
    if json_mode:
        kwargs["format"] = "json"
    try:
        return master.llm.chat(messages, **kwargs)
    except Exception as exc:
        return {"success": False, "error": type(exc).__name__}


EDIT_BLOCK = re.compile(r"<<<<<<< SEARCH\n([\s\S]*?)\n=======\n([\s\S]*?)\n>>>>>>> REPLACE", re.M)


def _select_files(master, state, cands: list[dict]) -> list[str] | None:
    """Pick 1-2 targets. Prefer paths named in the goal; shrink listing for local models."""
    from nexus.integrations.hermes_brain import _json_object
    goal = str(state.get("goal") or "")
    allowed = {c["path"] for c in cands}
    named = []
    for m in re.findall(r"(?<!\w)((?:nexus|tests)/[\w./-]+\.(?:py|html|css|js))\b", goal, re.I):
        norm = m.replace("\\", "/")
        if norm in allowed and norm not in named:
            named.append(norm)
    if named:
        state["selection_reason"] = "path_named_in_goal"
        return named[:2]
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", goal.lower()))
    def score(c):
        stem = Path(c["path"]).stem.lower()
        parts = set(re.split(r"[/_\.]", c["path"].lower())) | {stem}
        return sum(1 for t in tokens if t in parts or t in stem)
    ranked = sorted(cands, key=lambda c: (-score(c), c["path"]))
    short = ranked[:30] if len(ranked) > 30 else ranked
    top = [c["path"] for c in short if score(c) > 0][:2]
    if len(top) == 1 or (len(top) == 2 and score(next(c for c in short if c["path"] == top[0])) >= 2):
        state["selection_reason"] = "path_token_match:" + ",".join(top)
        return top[:2]
    listing = "\n".join(f"{c['path']} ({c['bytes']} B)" for c in short)
    _sys = (
        "You are Hermes, the NEXUS developer. Pick the 1 or 2 existing NEXUS files that "
        "must change to fulfil the goal. Only choose from the list. Answer exactly one JSON object: "
        "{\"files\":[\"<path>\"],\"reason\":\"<German sentence>\"}"
    )
    _les = hermes_lessons_block()
    if _les:
        _sys = _sys + "\n\n" + _les
    reply = _model(master, state, [
        {"role": "system", "content": _sys},
        {"role": "user", "content": "Goal: " + goal[:2000] + "\n\nEditable files:\n" + listing}],
        num_predict=300, json_mode=True)
    data = _json_object(str(reply.get("reply") or "")) if reply.get("success") else None
    files = [f for f in (data or {}).get("files") or [] if isinstance(f, str) and f in allowed][:2]
    state["selection_reason"] = str((data or {}).get("reason") or "")[:300]
    return files or None


FOCUS_LIMIT = 9_000  # Zeichen: Ziel, Regeln und Ausschnitt passen in das 4k-Kontextfenster des lokalen Modells


def _focus(path: str, content: str, goal: str) -> str:
    """Große Dateien: nur die im Ziel genannten Funktionen/Klassen wortgetreu zeigen.

    Die SEARCH-Blöcke werden weiterhin gegen die vollständige Datei angewendet; der Ausschnitt
    enthält nur unveränderte Originalzeilen. Kleine Dateien werden vollständig gezeigt.
    """
    if len(content) <= FOCUS_LIMIT:
        return f"File {path}:\n{content}"
    lines = content.splitlines(keepends=True)
    words = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b", goal))
    # Eindeutige Bezeichner (mit Unterstrich oder in Backticks) statt Alltagswörtern wie "run".
    names = {w for w in words if "_" in w} | set(re.findall(r"`([A-Za-z_]\w*)", goal)) or words
    blocks = []
    for i, line in enumerate(lines):
        m = re.match(r"(\s*)(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", line)
        if not m or m.group(2) not in names:
            continue
        indent, end = len(m.group(1)), i + 1
        while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent):
            end += 1
        blocks.append("".join(lines[i:end]))
    excerpt = "\n# ...\n".join(blocks)
    if not excerpt or len(excerpt) > FOCUS_LIMIT:
        return f"File {path} (first part):\n{content[:FOCUS_LIMIT]}"
    return (f"File {path} is large; these are the relevant parts, copied verbatim (SEARCH must use lines "
            f"from here):\n{excerpt}")


def _apply_block(content: str, search: str, replace: str) -> str | None:
    """SEARCH/REPLACE anwenden: exakt, sonst leerraumtolerant über ganze Zeilen.

    Toleranter Abgleich nur bei genau EINER passenden Zeilenfolge; die Einrückung des Originals
    wird übernommen und relativ auf REPLACE übertragen. Mehrdeutig oder fehlend -> None.
    """
    if content.count(search) == 1:
        return content.replace(search, replace, 1)
    lines = content.splitlines(keepends=True)
    want = [l.strip() for l in search.splitlines() if l.strip()]
    if not want:
        return None
    norm = [l.strip() for l in lines]
    hits = []
    for i in range(len(lines)):
        if norm[i] != want[0]:
            continue
        j, k = i, 0
        while j < len(lines) and k < len(want):
            if not norm[j]:
                j += 1
                continue
            if norm[j] != want[k]:
                break
            j, k = j + 1, k + 1
        if k == len(want):
            hits.append((i, j))
    if len(hits) != 1:
        return None
    start, end = hits[0]
    actual_indent = len(lines[start]) - len(lines[start].lstrip())
    rep_lines = replace.splitlines()
    first = next((l for l in rep_lines if l.strip()), "")
    shift = actual_indent - (len(first) - len(first.lstrip()))
    out = []
    for l in rep_lines:
        if not l.strip():
            out.append("\n")
        elif shift >= 0:
            out.append(" " * shift + l + "\n")
        else:
            out.append(l[min(-shift, len(l) - len(l.lstrip())):] + "\n")
    return "".join(lines[:start] + out + lines[end:])


def _edits(master, state, path: str, content: str, feedback: str = "") -> tuple[str | None, str]:
    _sys_edit = (
            "You are Hermes, editing ONE existing NEXUS file. Return only edit blocks in exactly this format, "
            "as many as needed, nothing else:\n<<<<<<< SEARCH\n<exact existing lines copied verbatim>\n=======\n"
            "<replacement lines>\n>>>>>>> REPLACE\nRules: SEARCH must match the current file exactly (whitespace "
            "included) and be unique; keep changes minimal; never touch authentication, approvals, permissions, "
            "tokens or security checks; no network calls; keep the file working."
    )
    _les_edit = hermes_lessons_block()
    if _les_edit:
        _sys_edit = _sys_edit + "\n\n" + _les_edit
    reply = _model(master, state, [
        {"role": "system", "content": _sys_edit},
        {"role": "user", "content": f"Goal: {state['goal'][:1500]}\n{feedback}\n\n" + _focus(path, content, state["goal"])}],
        num_predict=3000)
    if not reply.get("success"):
        return None, "MODEL_UNAVAILABLE"
    text = str(reply.get("reply") or "").replace("\r\n", "\n")
    blocks = EDIT_BLOCK.findall(text)
    if not blocks:
        return None, "NO_EDIT_BLOCKS"
    new = content
    for search, replace in blocks:
        applied = _apply_block(new, search, replace)
        if applied is None:
            return None, "SEARCH_NOT_UNIQUE_OR_MISSING: " + search[:120]
        new = applied
    return (new if new != content else None), ("" if new != content else "NO_CHANGE")


def _derive(master, state, root: Path, feedback: str = "") -> list[dict] | None:
    files = []
    for path in state["targets"]:
        raw = (root / path).read_bytes()
        content = raw.decode("utf-8").replace("\r\n", "\n")
        new, err = _edits(master, state, path, content, feedback)
        if new is None and err.startswith("SEARCH"):
            new, err = _edits(master, state, path, content, feedback + "\nPrevious attempt failed: " + err
                              + ". Copy SEARCH lines exactly from the file.")
        if new is None:
            state.setdefault("derive_errors", []).append({"path": path, "error": err})
            continue
        if b"\r\n" in raw:
            new = new.replace("\n", "\r\n")  # Zeilenenden der Originaldatei beibehalten (minimaler Diff)
        files.append({"path": path, "content": new})
    return files or None


def _invoke(task: str, params: dict) -> dict:
    from nexus.integrations.hermes_client import invoke
    return invoke({"operation": "submit", "task": task, "params": params})


def _patch_id(goal: str, round_no: int) -> str:
    return "PATCH-" + hashlib.sha256((goal + "|" + str(round_no)).encode("utf-8")).hexdigest()[:12]


def run(master, goal: str, session: dict) -> dict:
    from nexus.master.evidence import blocked
    text = str(goal or "").strip()
    root = Path(str(getattr(getattr(master, "project_fs", None), "root", "") or "."))
    path = _state_path(root, text)
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return blocked("NEXUS_ORDER_STATE_UNREADABLE", "Gespeicherter Fortschritt unlesbar; nichts wird geändert.")
    if state is None:
        state = {"schema": SCHEMA, "goal": text, "created_at": _utc(), "model_calls": 0, "targets": None,
                 "rounds": [], "apply": None, "restart": None, "status": "PLANNED"}
        _save(path, state)
    session["hermes_order"] = {"task": text, "requirements": [], "nexus": True}
    persist = getattr(master, "_persist_chat_order", None)
    if persist:
        try:
            persist()
        except Exception:
            pass

    if not state["targets"]:
        state["targets"] = _select_files(master, state, _candidates(root))
        _save(path, state)
        if not state["targets"]:
            state["status"] = "BLOCKED"; state["error"] = "NEXUS_TARGET_NOT_DERIVED"; _save(path, state)
            return _render(state)

    # Vorbereiten + isoliert testen, begrenzte Korrektur mit echter Testausgabe
    while not (state["rounds"] and state["rounds"][-1].get("test", {}).get("passed")):
        if len(state["rounds"]) > MAX_FIX_ROUNDS:
            break
        feedback = ""
        if state["rounds"]:
            last = state["rounds"][-1].get("test") or {}
            feedback = ("Previous patch failed the isolated NEXUS test (real output): "
                        + json.dumps({k: last.get(k) for k in ("regressions", "compile", "import", "details", "error")},
                                     ensure_ascii=False)[:3000])
        files = _derive(master, state, root, feedback)
        if not files:
            state["error"] = "NEXUS_EDITS_NOT_DERIVED"; break
        pid = _patch_id(text, len(state["rounds"]))
        prep = _invoke("hermes_prepare_nexus_patch", {"patch_id": pid, "goal": text[:500], "files": files})
        rnd = {"patch_id": pid, "prepare_task_id": prep.get("task_id"), "prepared": bool(prep.get("success")),
               "diff": ((prep.get("result") or {}).get("diff") or "")[:4000] if isinstance(prep.get("result"), dict) else "",
               "error": prep.get("error_code"), "test": None}
        state["rounds"].append(rnd)
        _save(path, state)
        if not rnd["prepared"]:
            state["error"] = "NEXUS_PATCH_NOT_PREPARED:" + str(prep.get("error_code")); break
        test = _invoke("hermes_test_nexus_patch", {"patch_id": pid})
        data = test.get("result") if isinstance(test.get("result"), dict) else {}
        prop = data.get("proposed") or {}
        rnd["test"] = {"task_id": test.get("task_id"), "passed": data.get("passed") is True,
                       "tests": data.get("tests"), "regressions": data.get("regressions"),
                       "compile": prop.get("compile"), "import": prop.get("import"),
                       "details": [d for t in (prop.get("tests") or {}).values() for d in t.get("details", [])][:4],
                       "error": test.get("error_code")}
        _save(path, state)
        if not test.get("success"):
            state["error"] = "NEXUS_PATCH_TEST_" + str(test.get("error_code")); break

    last = state["rounds"][-1] if state["rounds"] else {}
    if not (last.get("test") or {}).get("passed"):
        state["status"] = "BLOCKED" if not state["rounds"] else "PARTIAL"
        state.setdefault("error", "NEXUS_PATCH_TEST_NOT_PASSED")
        _save(path, state)
        return _render(state)

    # Freigabe → Anwenden → Verifizieren
    apply = state.get("apply") or {}
    if not apply.get("applied"):
        params = {"patch_id": last["patch_id"]}
        if apply.get("approval_id"):
            params["approval_id"] = apply["approval_id"]
        res = _invoke("nexus_apply_patch", params)
        data = res.get("result") if isinstance(res.get("result"), dict) else {}
        if data.get("approval_required"):
            state["apply"] = {"approval_id": data.get("approval_id"), "request_task_id": res.get("task_id"),
                              "applied": False}
            state["status"] = "PARTIAL"; state["error"] = "AWAITING_LOCAL_APPROVAL:" + str(data.get("approval_id"))
            _save(path, state)
            return _render(state)
        state["apply"] = {**apply, "task_id": res.get("task_id"), "applied": data.get("applied") is True and bool(res.get("success")),
                          "needs_restart": data.get("needs_restart"), "backup": data.get("backup"),
                          "error": res.get("error_code") or (res.get("result") or {}).get("message") if isinstance(res.get("result"), dict) else res.get("error_code")}
        _save(path, state)
        if not state["apply"]["applied"]:
            state["status"] = "PARTIAL"
            state["error"] = "APPLY_NOT_COMPLETED:" + str(state["apply"].get("error"))
            _save(path, state)
            return _render(state)
    # Live-Verifikation: Dateien entsprechen dem getesteten Vorschlag
    manifest = json.loads((root / "workspace/projects/hermes/nexus_patches" / last["patch_id"] / "manifest.json").read_text(encoding="utf-8"))
    live_ok = all(hashlib.sha256((root / f["path"]).read_bytes()).hexdigest() == f["proposed_sha256"] for f in manifest["files"])
    state["live_verified"] = live_ok
    if state["apply"].get("needs_restart"):
        restart = state.get("restart") or {}
        if not restart.get("done"):
            params = {"approval_id": restart["approval_id"]} if restart.get("approval_id") else {}
            res = _invoke("nexus_restart", params)
            data = res.get("result") if isinstance(res.get("result"), dict) else {}
            if data.get("approval_required"):
                state["restart"] = {"approval_id": data.get("approval_id"), "done": False}
                state["status"] = "PARTIAL"; state["error"] = "AWAITING_RESTART_APPROVAL:" + str(data.get("approval_id"))
                _save(path, state)
                return _render(state)
            state["restart"] = {**restart, "task_id": res.get("task_id"), "done": bool(data.get("scheduled")),
                                "scheduled_at": _utc()}
            state["status"] = "PARTIAL"; state["error"] = "RESTART_SCHEDULED_VERIFY_AFTER_RESTART"
            _save(path, state)
            return _render(state)
        # Nach dem Neustart: neue Instanz muss nach der Anwendung gestartet sein
        # Dieses Modul wird pro Prozess einmal geladen: ist es nach der Planung des Neustarts
        # geladen worden, läuft eine neue Instanz mit dem angewendeten Code.
        state["active_after_restart"] = bool(state["restart"].get("scheduled_at") and _LOADED_AT > state["restart"]["scheduled_at"])
    else:
        state["active_after_restart"] = None
    ok = live_ok and (state["active_after_restart"] is not False)
    state["status"] = "PASS" if ok else "PARTIAL"
    state["error"] = None if ok else ("LIVE_FILES_DIFFER" if not live_ok else "NOT_ACTIVE_AFTER_RESTART")
    _save(path, state)
    return _render(state)


def _render(state: dict) -> dict:
    lines = ["NEXUS-ÄNDERUNG (Selbstentwicklung als geprüfter Patch)"]
    if state.get("targets"):
        lines.append("Betroffene Dateien: " + ", ".join(state["targets"])
                     + (f" – Begründung: {state['selection_reason']}" if state.get("selection_reason") else ""))
    ids = []
    for i, r in enumerate(state.get("rounds") or []):
        t = r.get("test") or {}
        lines.append(f"Runde {i}: {r['patch_id']} vorbereitet (Task {r.get('prepare_task_id')}) – isolierter Test "
                     f"{t.get('task_id')}: {'BESTANDEN' if t.get('passed') else 'NICHT BESTANDEN'}"
                     + (f" – Regressionen: {', '.join(t.get('regressions') or [])}" if t.get("regressions") else "")
                     + (f" – Tests: {', '.join(t.get('tests') or [])}" if t.get("tests") else ""))
        ids += [x for x in (r.get("prepare_task_id"), t.get("task_id")) if x]
    for e in state.get("derive_errors") or []:
        lines.append(f"Änderung nicht ableitbar ({e['path']}): {e['error']}")
    ap = state.get("apply") or {}
    if ap.get("approval_id") and not ap.get("applied"):
        lines.append(f"FREIGABE ERFORDERLICH: {ap['approval_id']} unter http://127.0.0.1:8299/approvals – danach 'weiter'.")
    if ap.get("applied"):
        lines.append(f"Angewendet (Task {ap.get('task_id')}), Sicherung: {ap.get('backup')}, Live-Dateien = getesteter Vorschlag: {state.get('live_verified')}")
        ids.append(ap.get("task_id"))
    rs = state.get("restart") or {}
    if rs.get("approval_id") and not rs.get("done"):
        lines.append(f"AKTIVIERUNG: Neustart-Freigabe {rs['approval_id']} unter /approvals – danach 'weiter'.")
    if rs.get("done"):
        lines.append(f"Neustart geplant (Task {rs.get('task_id')}); aktiv nach Neustart: {state.get('active_after_restart')}")
    lines.append("Rückweg: nexus_rollback_patch (PRIVILEGED) mit derselben Patch-ID stellt die Sicherung wieder her.")
    status = state.get("status") or "BLOCKED"
    lines.append(status + (": " + str(state.get("error")) if state.get("error") else ": Änderung angewendet und verifiziert."))
    return {"success": status == "PASS", "status": status, "intent": "hermes_work", "reply": "\n".join(lines),
            "error": state.get("error"),
            "data": {"execution_status": status, "task_ids": [i for i in ids if i], "order_type": "nexus_order",
                     "patch_id": (state.get("rounds") or [{}])[-1].get("patch_id"),
                     "approval_id": ap.get("approval_id") if not ap.get("applied") else rs.get("approval_id"),
                     "provenance": "persisted_bridge_tasks"}}
