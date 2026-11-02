"""Hermes-Codeentwicklung: Ziel → geprüfter Python-Dateiplan → Codeerstellung
→ statische Prüfung → (freigegebener) begrenzter Testlauf → Bericht.

Keine zweite Agentenarchitektur: NEXUS bleibt die Steuerung. Geschrieben und
geprüft wird ausschließlich über HermesBrain (create_file/read_file) und die
registrierten Bridge-Aufgaben hermes_check_code und hermes_run_code_tests.

Ein freies Entwicklungsziel ist keine pauschale Freigabe: jede Datei wird gegen
den Codebereich geprüft, und ein Testlauf startet nur mit nachgewiesener
OS-Containment-Grenze und ausdrücklicher Freigabe (NEXUS_CODE_EXEC=1). Ohne diese
Freigabe bleibt es bei Codeerstellung + statischer Prüfung.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "nexus.hermes-code-order.v1"
BATCH_SIZE = 4  # HermesBrain-Auftragsvertrag: 1..4 Anforderungen je Lauf
MAX_PLAN_FILES = 6
STATE_DIR = "workspace/state"
STATE_PREFIX = "hermes-code-order-"
CODE_DIR = "workspace/projects/hermes/code"
REPORT_NAME = "bericht.json"
ACTION = "create_file → read_file → bytegenauer Vergleich"
ISOLATION_NOTE = ("Testläufe im Windows-AppContainer (kein Netzwerk inkl. Loopback, nur Laufkopie beschreibbar, "
                  "Benutzerprofil/Tokens/NEXUS-Quellcode unlesbar) + Job Object (Speicher, CPU, Prozesse, Laufzeit); "
                  "Isolation per Ausbruchs-Selbsttest belegt. Ohne bestandenen Selbsttest wird nichts ausgeführt.")

CREATE_INTENT = re.compile(
    r"\b(erstell\w*|schreib\w*|speicher\w*|erzeug\w*|entwick\w*|implementier\w*|"
    r"programmier\w*|bau\w*|codier\w*|create|write|build|develop|implement)\b", re.I)
CODE_SIGNAL = re.compile(
    r"\b(programm\w*|software|skript\w*|script\w*|\w*modul\w*|funktion\w*|klasse\w*|"
    r"algorithm\w*|quellcode|\bcode\b|\w*rechner|bibliothek\w*|library|anwendung\w*|"
    r"\bapp\w*|\bcli\b|\bbot\b|parser|konverter|\w*umrechner|webseite|website|web-?app)\b", re.I)
# Mehrdeutige Software-Nomen, die erst mit einem starken Bau-Verb (bauen/entwickeln/
# programmieren/implementieren) zu einem Entwicklungsauftrag werden ("Baue ein Spiel.").
AMBIGUOUS_CODE = re.compile(r"\b(spiel\w*|game\w*|\btool\b|werkzeug)\b", re.I)
STRONG_BUILD = re.compile(
    r"\b(bau\w*|entwick\w*|programmier\w*|implementier\w*|codier\w*|create|build|develop|implement)\b", re.I)
# Eine Liste/Uebersicht ist ein Lese-, kein Entwicklungsauftrag.
LIST_OBJECT = re.compile(r"\b(liste|aufz(?:ä|ae)hlung|übersicht|uebersicht|zusammenfassung|überblick|summary)\b", re.I)
# Eine Verneinung verbietet nur den Schreib-/Bauakt selbst ("erstelle keine Python-Datei",
# "nicht erneut entwickeln"), nie Qualitätsvorgaben wie "keine simulierten Funktionen" oder
# "keine erfundenen Tests". Ein Auftrag gilt nur als verneint, wenn JEDER Schreib-/Bauakt
# verneint ist; ein einziger positiver Imperativ hält den Auftrag gültig.
_ACT = r"(?:erstell|schreib|speicher|programmier|entwick|bau|implementier|codier|anleg)\w*"
_ARTIFACT = r"(?:datei\w*|code|programm\w*|skript\w*|script\w*|python\w*|modul\w*|py|webseite|website|spiel\w*|game\w*|app\w*)"
NEGATED_ACT = re.compile(
    r"\b(?:nicht|niemals)[,\s]+(?:\w+[,\s]+){0,3}?" + _ACT
    + r"|\bkein(?:e|en|er|es)?\s+(?:[\w]+[\s-]+){0,2}?" + _ARTIFACT + r"\s+(?:\w+\s+)?" + _ACT
    + r"|\b" + _ACT + r"\s+(?:\w+\s+){0,2}?(?:kein(?:e|en|er|es)?|nicht)\s+(?:[\w]+[\s-]+)?" + _ARTIFACT + r"\b",
    re.I)
_ACT_WORD = re.compile(r"\b" + _ACT, re.I)


def write_negated(text: str) -> bool:
    """True nur, wenn jeder Schreib-/Bauakt des Textes ausdrücklich verneint ist."""
    low = str(text or "").lower()
    acts = [m.start() for m in _ACT_WORD.finditer(low)]
    if not acts:
        return False
    spans = [(m.start(), m.end()) for m in NEGATED_ACT.finditer(low)]
    return all(any(start <= act < end for start, end in spans) for act in acts)


# Nur bejahte, aktive Bau-Verben machen einen Entwicklungsauftrag: "keine neue Architektur
# entwickeln", "wurde bereits implementiert" oder "soll nicht erneut entwickelt werden"
# beschreiben Grenzen bzw. Vorhandenes, keinen neuen Bauauftrag.
_NEG_ANY = re.compile(r"\b(?:nicht|niemals|kein\w*)[,\s]+(?:[\w-]+[,\s]+){0,3}?" + _ACT, re.I)
_PASSIVE_BEFORE = re.compile(
    r"\b(?:wurde|wurden|worden|ist|sind|war|waren|wird|bereits|schon)\b(?:\s+[\w-]+){0,3}\s*$", re.I)
_PASSIVE_AFTER = re.compile(r"\s*(?:werden|worden|wurde|ist)\b", re.I)


def affirmative_build(text: str) -> bool:
    """Gibt es mindestens ein bejahtes, aktives Bau-Verb (kein Passiv, keine Verneinung)?"""
    low = str(text or "").lower()
    negated = [(m.start(), m.end()) for m in _NEG_ANY.finditer(low)]
    negated += [(m.start(), m.end()) for m in NEGATED_ACT.finditer(low)]
    for m in STRONG_BUILD.finditer(low):
        start = m.start()
        if any(a <= start < b for a, b in negated):
            continue
        if _PASSIVE_BEFORE.search(low[max(0, start - 60):start]) or _PASSIVE_AFTER.match(low, m.end()):
            continue
        return True
    return False


QUESTION = re.compile(r"\b(wie\s+(?:erstelle|schreibe|programmiere|baue)\w*|beispiel|erkl(?:ä|ae)re)\b", re.I)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_code_order(text: str, named_paths=()) -> bool:
    """Ein Entwicklungsauftrag mit Python-/Software-Ziel und ohne festgelegten Inhalt."""
    from nexus.master.project_order import has_explicit_content

    low = (text or "").lower()
    if not CREATE_INTENT.search(low) or has_explicit_content(text):
        return False
    if write_negated(low) or QUESTION.search(low) or LIST_OBJECT.search(low):
        return False
    if any(str(p).lower().endswith(".py") for p in (named_paths or []) if p) or ".py" in low:
        return True
    # Nur Textdateien benannt und kein starkes Bau-Verb: ein Dokument über "Funktionen" ist
    # kein Code ("Erstelle die Datei a.md und beschreibe die Funktionen").
    if [p for p in (named_paths or []) if p] and not affirmative_build(low):
        return False
    if affirmative_build(low) and AMBIGUOUS_CODE.search(low):
        return True
    return bool(CODE_SIGNAL.search(low))


def _state_path(root: Path, goal: str) -> Path:
    directory = root / STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:16]
    return directory / (STATE_PREFIX + digest + ".json")


def _load_state(path: Path):
    """None = noch kein Auftrag; dict = gespeicherter Auftrag; Fehlschlag wird nicht überschrieben."""
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return value if isinstance(value, dict) and value.get("schema") == SCHEMA else False


def _save_state(path: Path, state: dict) -> None:
    from nexus.common.atomic import atomic_write_json
    state["updated_at"] = _utc()
    atomic_write_json(path, state)


def _report_path(project: str) -> str:
    return f"{CODE_DIR}/{project}/{REPORT_NAME}"


MAX_FIX_ROUNDS = 3


def _run_refused(run_state: dict) -> bool:
    """Testlauf wurde gar nicht ausgeführt (Sperre/Containment fehlte), kein Codefehler.

    Ein solcher Lauf darf bei Wiederaufnahme neu angestoßen werden, sobald die belegte
    Isolation verfügbar ist; ein tatsächlich ausgeführter Lauf wird nie wiederholt.
    """
    result = run_state.get("result") if isinstance(run_state.get("result"), dict) else {}
    executed = isinstance(result.get("returncode"), int) or bool(result.get("containment"))
    refused = (run_state.get("error") == "ACTION_DENIED"
               or "CODE_EXECUTION_DISABLED" in str(result.get("message") or result.get("error") or ""))
    return run_state.get("status") != "succeeded" and not executed and refused


def _run_failed_in_code(run_state: dict) -> bool:
    """Nur ein tatsächlich ausgeführter, fehlgeschlagener (oder aussageloser) Test ist ein Codefehler."""
    result = run_state.get("result") if isinstance(run_state.get("result"), dict) else {}
    executed = result.get("containment") == "windows_appcontainer" and isinstance(result.get("returncode"), int)
    return executed and (run_state.get("status") != "succeeded" or run_state.get("meaningful") is False)


def test_meaningful(test_source: str, modules: list[str]) -> str | None:
    """Statische Prüfung: Ein Testlauf mit returncode 0 belegt nur etwas, wenn das Testmodul ein
    Projektmodul importiert UND beim direkten Start tatsächlich Prüfungen ausführt.
    Gibt den Grund zurück, warum der Test aussagelos ist, sonst None."""
    import ast
    try:
        tree = ast.parse(test_source)
    except SyntaxError as exc:
        return "TestNotMeaningful: syntax error " + str(exc)[:120]
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    if not imported & set(modules):
        return ("TestNotMeaningful: the test module imports none of the project modules "
                f"({', '.join(modules)}); add e.g. `from {modules[0] if modules else 'main'} import <function>`.")
    tests = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test")}
    top_asserts = any(isinstance(n, ast.Assert) for n in tree.body)
    called = set()
    for node in tree.body:
        if (isinstance(node, ast.If) and "__main__" in ast.unparse(node.test)) or isinstance(node, ast.Expr):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                    called.add(sub.func.id)
                if isinstance(sub, ast.Attribute) and sub.attr == "main" and "unittest" in ast.unparse(sub):
                    called |= tests
    if not top_asserts and not (tests & called):
        return ("TestNotMeaningful: the test functions are never executed when the file runs as "
                "`python test_x.py`; call every test_* function inside `if __name__ == '__main__':`.")
    return None


def _error_signature(output: str) -> str:
    """Fehlerart + Fehlerstellen (Datei:Zeile) ohne Laufverzeichnis: gleich bei unverändertem Fehler."""
    frames = re.findall(r'File "[^"]*[\\/]([\w-]+\.py)", line (\d+)', output)
    last = [l for l in output.strip().splitlines() if l.strip()][-1:] or [""]
    return hashlib.sha256((repr(frames) + "|" + last[0].split(":")[0]).encode("utf-8")).hexdigest()[:16]


def _fix_round(master, state: dict, root: Path) -> dict:
    from nexus.integrations.hermes_client import invoke
    from nexus.integrations.hermes_brain import _strip_code_fence

    result = state["run"].get("result") or {}
    output = (str(result.get("stderr") or "") + "\n" + str(result.get("stdout") or ""))[-3500:]
    if state["run"].get("meaningful") is False:
        output = (output.strip() + "\n" + str(state["run"].get("meaningful_reason") or "")).strip()
    names = {r["path"].rsplit("/", 1)[-1]: r["path"] for r in state["files"] if r["path"].endswith(".py")}
    # Letzte Traceback-Zeile mit einer Projektdatei bestimmt das Korrekturziel.
    hits = [m.group(1) for m in re.finditer(r'File "[^"]*[\\/]([\w-]+\.py)"', output) if m.group(1) in names]
    target_name = hits[-1] if hits else state["test_path"].rsplit("/", 1)[-1]
    # Bleibt dieselbe Fehlerstelle nach einer Korrektur dieser Datei unverändert, liegt die Ursache
    # in einer anderen beteiligten Datei (z. B. ein Test, der eine interaktive Schleife aufruft).
    signature = _error_signature(output)
    tried = [f for f in state.get("fixes", []) if f.get("ok") and f.get("signature") == signature]
    if tried and tried[-1].get("path") == names.get(target_name):
        others_hit = [h for h in reversed(hits) if names[h] != tried[-1]["path"]]
        alternative = others_hit[0] if others_hit else state["test_path"].rsplit("/", 1)[-1]
        if names.get(alternative, state["test_path"]) != tried[-1]["path"]:
            target_name = alternative
    target = names.get(target_name, state["test_path"])
    current = (root / target).read_text(encoding="utf-8")
    others = "\n\n".join(f"# {n}\n{(root / p).read_text(encoding='utf-8')[:6000]}" for n, p in names.items() if p != target)
    fixed, method = _import_repair(output, target, current, names, root), "deterministic_import"
    if fixed is not None:
        return _apply_fix(state, target, current, fixed, output, signature, method)
    llm = getattr(master, "llm", None)
    if llm is None:
        return {"ok": False, "path": target, "error": "MODEL_UNAVAILABLE"}
    # Gezielte Hinweise je echter Fehlerart aus der Testausgabe.
    hints = []
    if "EOFError" in output or "input(" in current:
        hints.append("The test run has no keyboard: move the logic into pure functions with parameters "
                     "(e.g. check_guess(guess, secret) -> str, play(guesses, secret) -> list of messages) and keep "
                     "input() only inside main() behind `if __name__ == '__main__':`. Tests call the pure functions "
                     "with fixed values, never main(), never random numbers without a fixed secret.")
    if "AssertionError" in output and target == state["test_path"]:
        hints.append("The assertion in the test is wrong or depends on randomness/I/O. Test the pure functions of "
                     "the other files with fixed inputs and exact expected values that follow from that code.")
    is_test = target == state["test_path"]
    if is_test:
        mains = [n[:-3] for n, p in names.items() if p != target]
        hints.append(f"This is the TEST file. Import the functions to test from {', '.join(mains) or 'the main module'} "
                     f"(e.g. `from {mains[0] if mains else 'main'} import compare_numbers`). Do NOT copy their code into "
                     "the test, do NOT define or call main(), never call input(). Plain asserts with fixed values, "
                     "test functions called under `if __name__ == '__main__':`, then print a short success line.")
    how = ("How to fix:\n- " + "\n- ".join(hints) + "\n\n") if hints else ""
    messages = [
        {"role": "system", "content":
            "You fix ONE file of a small Python project. A real isolated test run failed; the output is below. "
            "Fix the root cause in this file only. Tests must run non-interactively: never call input(), never "
            "wait for the user, never use the network. Use only the Python standard library. Output ONLY the "
            "complete corrected file content, no markdown fence, no explanation."},
        {"role": "user", "content": f"Goal: {state['goal'][:1500]}\n\nReal test output:\n{output}\n\n{how}"
                                    f"File to fix: {target_name}\n{current}\n\nOther project files:\n{others}"}]
    fixed = None
    for attempt in range(2):
        reply = llm.chat(messages, temperature=0, num_predict=4096)
        if not reply.get("success"):
            return {"ok": False, "path": target, "error": "MODEL_UNAVAILABLE"}
        fixed = _strip_code_fence(str(reply.get("reply") or "")).rstrip("\n") + "\n"
        # Ein Test, der weiter input() aufruft, scheitert sicher erneut: einmal mit klarem Befund
        # nachfordern statt eine Prüfrunde zu verbrauchen.
        problem = None
        if is_test and re.search(r"\binput\s*\(|\bmain\s*\(\s*\)", fixed):
            problem = "the test still defines/calls main() or input()"
        elif is_test and test_meaningful(fixed, mains):
            problem = test_meaningful(fixed, mains)
        if problem and attempt == 0:
            messages = messages + [{"role": "assistant", "content": fixed[:6000]},
                                   {"role": "user", "content": "Rejected: " + problem + ". Output the test file "
                                    "again: import the pure functions from the project module, no input(), no "
                                    "main(), and call every test_* function under `if __name__ == '__main__':`."}]
            continue
        break
    if not fixed.strip() or fixed == current:
        return {"ok": False, "path": target, "error": "FIX_NOT_GENERATED"}
    return _apply_fix(state, target, current, fixed, output, signature, "hermes_model")


def _import_repair(output: str, target: str, current: str, names: dict, root: Path) -> str | None:
    """Deterministische Standardreparatur: NameError für einen Namen, den ein anderes Projektmodul
    definiert -> fehlenden Import in genau der betroffenen Datei ergänzen (wie ein Linter-Autofix)."""
    m = re.search(r"NameError: name '([A-Za-z_]\w*)' is not defined", output)
    if not m:
        return None
    name = m.group(1)
    for file_name, path in names.items():
        if path == target:
            continue
        text = (root / path).read_text(encoding="utf-8")
        if re.search(r"^(?:def|class)\s+" + re.escape(name) + r"\b|^" + re.escape(name) + r"\s*=", text, re.M):
            line = f"from {file_name[:-3]} import {name}\n"
            if line in current:
                return None
            lines = current.splitlines(keepends=True)
            pos = max((i + 1 for i, l in enumerate(lines) if re.match(r"(?:import|from)\s+\w", l)), default=0)
            return "".join(lines[:pos] + [line] + lines[pos:])
    return None


def _apply_fix(state: dict, target: str, current: str, fixed: str, output: str, signature: str, method: str) -> dict:
    from nexus.integrations.hermes_client import invoke
    patch = invoke({"operation": "submit", "task": "patch_file",
                    "params": {"path": target, "old_text": current, "new_text": fixed}})
    read = invoke({"operation": "submit", "task": "read_file", "params": {"path": target}}) if patch.get("success") else {}
    content = (read.get("result") or {}).get("content") if isinstance(read.get("result"), dict) else None
    static = invoke({"operation": "submit", "task": "hermes_check_code", "params": {"path": target}}) if content == fixed else {}
    ok = bool(patch.get("success")) and content == fixed and static.get("status") == "succeeded"
    out = {"ok": ok, "path": target, "patch_task_id": patch.get("task_id"), "read_task_id": read.get("task_id"),
           "static_task_id": static.get("task_id"), "trigger": output[-600:], "signature": signature,
           "method": method,
           "error": None if ok else (patch.get("error_code") or static.get("error_code") or "FIX_NOT_VERIFIED")}
    if not ok and content == fixed:
        # Korrektur verschlechtert den geprüften Stand (z. B. Syntaxfehler): zuletzt guten Stand
        # über denselben Patch-Weg wiederherstellen, statt eine kaputte Datei stehen zu lassen.
        back = invoke({"operation": "submit", "task": "patch_file",
                       "params": {"path": target, "old_text": fixed, "new_text": current}})
        check = invoke({"operation": "submit", "task": "read_file", "params": {"path": target}}) if back.get("success") else {}
        restored = (check.get("result") or {}).get("content") if isinstance(check.get("result"), dict) else None
        out["reverted"] = {"patch_task_id": back.get("task_id"), "read_task_id": check.get("task_id"),
                           "ok": restored == current}
    return out


def _assess_run(state: dict, root: Path) -> None:
    """Aussagekraft eines erfolgreichen Testlaufs separat bewerten (der Bridge-Beleg bleibt unverändert)."""
    run_state = state["run"]
    if run_state.get("status") != "succeeded":
        return
    test = root / state["test_path"]
    modules = [r["path"].rsplit("/", 1)[-1][:-3] for r in state["files"]
               if r["path"].endswith(".py") and r["path"] != state["test_path"]]
    reason = test_meaningful(test.read_text(encoding="utf-8"), modules) if test.is_file() else "TestNotMeaningful: missing"
    run_state["meaningful"] = reason is None
    run_state["meaningful_reason"] = reason


def _aggregate(state: dict, writes_ok: bool, static_ok: bool, run_state) -> str:
    if not state.get("batches"):
        return "PLANNED"
    if not writes_ok:
        return "PARTIAL" if any(b.get("status") == "PASS" for b in state["batches"]) else "BLOCKED"
    if not static_ok:
        return "PARTIAL" if state.get("static") else "BLOCKED"
    if not run_state:
        return "PARTIAL"
    return "PASS" if run_state.get("status") == "succeeded" and run_state.get("meaningful") is not False else "PARTIAL"


def plan_steps(state: dict) -> list[dict]:
    """Ein geprüfter Arbeitsschritt je geplanter Datei, mit echten Werkzeugbelegen."""
    checks = {c.get("path"): c for b in state.get("batches", []) for c in b.get("verification", [])}
    owner = {r.get("path"): b for b in state.get("batches", []) for r in b.get("requirements", [])}
    steps = []
    for number, requirement in enumerate(state.get("files", []), 1):
        path = requirement.get("path")
        check = checks.get(path)
        batch = owner.get(path)
        if check:
            status = "PASS"
            ids = [check.get("create_task_id"), check.get("read_task_id")]
        elif batch:
            status = str(batch.get("status") or "PLANNED")
            ids = []
        else:
            status = "PLANNED"
            ids = []
        steps.append({"id": number, "path": path, "action": ACTION, "status": status,
                      "task_ids": [i for i in ids if i]})
    return steps


def _report_content(state: dict) -> str:
    def cap(value, limit):
        text = "" if value is None else str(value)
        return text[:limit]

    run_state = state.get("run") or {}
    result = run_state.get("result") if isinstance(run_state.get("result"), dict) else {}
    payload = {
        "schema": "nexus.hermes-code-report.v1",
        "goal": state.get("goal"),
        "plan_id": state.get("plan_id"),
        "project": state.get("project"),
        "files": [r.get("path") for r in state.get("files", [])],
        "static": [{"path": s.get("path"), "status": s.get("status"),
                    "external": (s.get("result") or {}).get("external"),
                    "missing": (s.get("result") or {}).get("missing"),
                    "forbidden": (s.get("result") or {}).get("forbidden")}
                   for s in state.get("static", [])],
        "run": {
            "status": run_state.get("status"),
            "task_id": run_state.get("task_id"),
            "returncode": result.get("returncode"),
            "containment": result.get("containment"),
            "network_guard": result.get("network_guard"),
            "filesystem_isolation": result.get("filesystem_isolation"),
            "timed_out": result.get("timed_out"),
            "stdout_excerpt": cap(result.get("stdout"), 1500),
            "stderr_excerpt": cap(result.get("stderr"), 1000),
        },
        "overall_status": state.get("status"),
        "isolation_note": ISOLATION_NOTE,
        "created_at": _utc(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render(state: dict) -> dict:
    from nexus.master.evidence import _response

    fixes = [f for f in state.get("fixes", []) if f.get("ok")]
    fixed_paths = {f["path"] for f in fixes}
    requirements = [r for b in state.get("batches", []) for r in b.get("requirements", [])
                    if r["path"] not in fixed_paths]
    verification = [c for b in state.get("batches", []) for c in b.get("verification", [])
                    if c.get("path") not in fixed_paths]
    superseded = []
    write_ids = []
    for c in (c for b in state.get("batches", []) for c in b.get("verification", [])):
        ids = [t for t in (c.get("create_task_id"), c.get("read_task_id")) if t]
        (superseded if c.get("path") in fixed_paths else write_ids).extend(ids)
    static_ids = [s.get("task_id") for s in state.get("static", []) if s.get("task_id")
                  and s.get("path") not in fixed_paths]
    last_fix = {}
    for f in fixes:
        if f["path"] in last_fix:
            superseded += [t for t in (last_fix[f["path"]].get("patch_task_id"), last_fix[f["path"]].get("read_task_id"),
                                       last_fix[f["path"]].get("static_task_id")) if t]
        last_fix[f["path"]] = f
    fix_ids = [t for f in last_fix.values() for t in (f.get("patch_task_id"), f.get("read_task_id"), f.get("static_task_id")) if t]
    superseded += [s.get("task_id") for s in state.get("static", []) if s.get("task_id") and s.get("path") in fixed_paths]
    superseded += [r.get("task_id") for r in state.get("superseded_runs", []) if r.get("task_id")]
    run_ids = [state["run"]["task_id"]] if state.get("run") and state["run"].get("task_id") else []
    report_ids = [t for t in (state.get("report") or {}).get("task_ids", []) if t]
    task_ids = write_ids + static_ids + fix_ids + run_ids + report_ids
    error = next((b.get("error") for b in state.get("batches", []) if b.get("error")), None)
    out = _response({
        "status": state.get("status") or "BLOCKED",
        "task_ids": task_ids,
        "requirements": requirements,
        "verification": verification,
        "error": error,
        "model": state.get("model"),
        "run_id": state.get("plan_id"),
    })
    steps = plan_steps(state)
    data = out.get("data") if isinstance(out.get("data"), dict) else {}
    data.update(order_type="code_order", plan=steps, plan_id=state.get("plan_id"),
                plan_status=state.get("status"), goal=state.get("goal"), project=state.get("project"),
                static=state.get("static"), run=state.get("run"), report=state.get("report"),
                fixes=state.get("fixes"), superseded_task_ids=superseded,
                isolation_note=ISOLATION_NOTE,
                provenance=data.get("provenance") or "persisted_bridge_tasks")
    out["data"] = data
    out["plan"] = steps
    header = ["ENTWICKLUNGSAUFTRAG (Code-Plan " + str(state.get("plan_id")) + ", "
              + str(len(steps)) + " Dateien):"]
    for step in steps:
        line = "{id}. {path} – {action} – {status}".format(**step)
        if step["task_ids"]:
            line += " – Task-IDs: " + ", ".join(step["task_ids"])
        header.append(line)
    if state.get("static"):
        def static_label(item):
            label = f"{item.get('path')}={item.get('status')}"
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            extra = [f"{key}={','.join(result[key])}" for key in ("external", "missing", "forbidden")
                     if result.get(key)]
            return label + (" (" + "; ".join(extra) + ")" if extra else "")
        header.append("Statische Prüfung: " + ", ".join(static_label(s) for s in state["static"]))
    run_state = state.get("run")
    if run_state:
        result = run_state.get("result") if isinstance(run_state.get("result"), dict) else {}
        header.append("Testlauf: {tid} – returncode={rc} – containment={cont} – network={net} – fs={fs}{to}".format(
            tid=run_state.get("task_id"), rc=result.get("returncode"),
            cont=result.get("containment"), net=result.get("network_guard"),
            fs=result.get("filesystem_isolation"),
            to=" – TIMEOUT" if result.get("timed_out") else ""))
        if run_state.get("meaningful") is False:
            header.append("Testlauf ohne Aussagekraft (kein PASS): " + str(run_state.get("meaningful_reason"))[:300])
        if run_state.get("status") != "succeeded":
            # Konkrete Grenze sichtbar machen (z. B. CODE_EXECUTION_DISABLED), nicht nur "failed".
            cause = str(result.get("message") or run_state.get("error") or result.get("error") or "unbekannt")
            header.append("Testlauf nicht ausgeführt/bestanden: " + cause[:200]
                          + (" – Codeausführung ist ohne lokale Freigabe (NEXUS_CODE_EXEC=1) gesperrt; "
                             "Ergebnis bleibt PARTIAL." if "CODE_EXECUTION_DISABLED" in cause else ""))
    for number, f in enumerate(state.get("fixes", []), 1):
        header.append(f"Korrektur {number} ({f.get('path')}, {f.get('method') or 'hermes_model'}): patch_file {f.get('patch_task_id')} + read_file "
                      f"{f.get('read_task_id')} + statische Prüfung {f.get('static_task_id')} – "
                      + ("OK" if f.get("ok") else str(f.get("error"))))
    for old in state.get("superseded_runs", []):
        res = old.get("result") if isinstance(old.get("result"), dict) else {}
        header.append(f"Früherer Testlauf {old.get('task_id')}: returncode={res.get('returncode')} – überholt")
    if state.get("report"):
        header.append("Bericht: " + str(state["report"].get("path")) + " – " + str(state["report"].get("status")))
    header.append("Hinweis: " + ISOLATION_NOTE)
    header.append("")
    out["reply"] = "\n".join(header) + "\n" + str(out.get("reply") or "")
    return out


def run(master, goal: str, session: dict) -> dict:
    """Entwicklungsauftrag planen, Code erstellen, statisch prüfen, begrenzt testen, berichten."""
    from nexus.integrations.hermes_brain import HermesBrain
    from nexus.integrations.hermes_client import invoke
    from nexus.master.evidence import blocked

    text = str(goal or "").strip()
    if not text or len(text) > 6000:
        return blocked("CODE_GOAL_REQUIRED", "Für diesen Entwicklungsauftrag fehlt ein eindeutiges Ziel.")

    root = Path(str(getattr(getattr(master, "project_fs", None), "root", "") or "."))
    path = _state_path(root, text)
    state = _load_state(path)
    if state is False:
        return blocked("CODE_ORDER_STATE_UNREADABLE",
                       "Der gespeicherte Fortschritt dieses Entwicklungsauftrags ist nicht lesbar. "
                       "Es wird nichts neu abgeleitet und nichts geschrieben.")
    if state is None:
        derived = HermesBrain(master).derive_code_plan(text, max_files=MAX_PLAN_FILES)
        if not derived.get("success"):
            code = str(derived.get("error") or "CODE_PLAN_NOT_DERIVED")
            out = blocked("CODE_PLAN_NOT_DERIVED:" + code,
                          "Der Entwicklungsauftrag konnte nicht in einen geprüften Python-Dateiplan "
                          "übersetzt werden (" + code + "). Es wurde keine Datei geschrieben.")
            if isinstance(out.get("data"), dict):
                out["data"]["plan_error"] = code
            return out
        state = {
            "schema": SCHEMA,
            "goal": text,
            "plan_id": "CODE-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
            "created_at": _utc(),
            "model": derived.get("model"),
            "project": derived["project"],
            "files": derived["files"],
            "main_paths": derived["main_paths"],
            "test_path": derived["test_path"],
            "batches": [],
            "static": [],
            "run": None,
            "report": None,
            "status": "PLANNED",
        }
        _save_state(path, state)

    session["hermes_order"] = {"task": text, "requirements": state["files"],
                               "code": True, "project": state["project"]}
    persist = getattr(master, "_persist_chat_order", None)
    if persist:
        try:
            persist()
        except Exception:
            pass

    files = state["files"]
    for index in range(0, len(files), BATCH_SIZE):
        batch_index = index // BATCH_SIZE
        batch = files[index:index + BATCH_SIZE]
        existing = state["batches"][batch_index] if batch_index < len(state["batches"]) else None
        if existing and existing.get("status") == "PASS":
            continue  # belegter Abschluss steht; kein zweiter Schreibversuch.
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
        _save_state(path, state)
        if record["status"] != "PASS":
            break

    planned_batches = math.ceil(len(files) / BATCH_SIZE)
    writes_ok = (len(state["batches"]) == planned_batches
                 and all(b.get("status") == "PASS" for b in state["batches"]))

    if writes_ok:
        py_files = [r["path"] for r in files if r["path"].lower().endswith(".py")]
        done = {s.get("path") for s in state.get("static", []) if s.get("status") == "succeeded"}
        for py_path in py_files:
            if py_path in done:
                continue  # bereits belegte Prüfung wird wiederverwendet, nicht wiederholt.
            res = invoke({"operation": "submit", "task": "hermes_check_code", "params": {"path": py_path}})
            entry = {"path": py_path, "task_id": res.get("task_id"),
                     "status": res.get("status"), "verification": res.get("verification"),
                     "result": res.get("result"), "error": res.get("error_code")}
            state["static"] = [s for s in state.get("static", []) if s.get("path") != py_path] + [entry]
            _save_state(path, state)
    static_ok = bool(state.get("static")) and all(s.get("status") == "succeeded" for s in state.get("static", []))

    if writes_ok and static_ok and state.get("run") and _run_refused(state["run"]):
        # Früher verweigerter Lauf (z. B. vor Freischaltung der AppContainer-Isolation): kein
        # Codefehler, daher neu anstoßen; der alte Beleg bleibt als überholt sichtbar.
        state["superseded_runs"] = state.get("superseded_runs", []) + [dict(state["run"], superseded_reason="run_refused")]
        state["run"] = None
        state["report"] = None
        _save_state(path, state)
    if writes_ok and static_ok and state.get("run") is None:
        res = invoke({"operation": "submit", "task": "hermes_run_code_tests",
                      "params": {"path": state["test_path"]}})
        state["run"] = {"path": state["test_path"], "task_id": res.get("task_id"),
                        "status": res.get("status"), "verification": res.get("verification"),
                        "result": res.get("result"), "error": res.get("error_code")}
        _assess_run(state, root)
        _save_state(path, state)

    if state.get("run") and "meaningful" not in state["run"]:
        _assess_run(state, root)  # Wiederaufnahme: auch frühere Läufe bewerten
    # Echte Testausgabe → Hermes korrigiert genau die betroffene Datei → isolierter Neulauf.
    while (writes_ok and static_ok and state.get("run") and _run_failed_in_code(state["run"])
           and len(state.setdefault("fixes", [])) < MAX_FIX_ROUNDS):
        fix = _fix_round(master, state, root)
        state["fixes"].append(fix)
        _save_state(path, state)
        if not fix.get("ok"):
            break
        res = invoke({"operation": "submit", "task": "hermes_run_code_tests",
                      "params": {"path": state["test_path"]}})
        state["superseded_runs"] = state.get("superseded_runs", []) + [state["run"]]
        state["run"] = {"path": state["test_path"], "task_id": res.get("task_id"),
                        "status": res.get("status"), "verification": res.get("verification"),
                        "result": res.get("result"), "error": res.get("error_code")}
        _assess_run(state, root)
        state["report"] = None  # Bericht muss den neuen Stand belegen
        _save_state(path, state)

    state["status"] = _aggregate(state, writes_ok, static_ok, state.get("run"))

    report = state.get("report")
    if writes_ok and not (isinstance(report, dict) and report.get("status") == "PASS"):
        content = _report_content(state)
        report_req = {"path": _report_path(state["project"]), "content": content}
        existing = root / report_req["path"]
        if existing.is_file() and existing.read_text(encoding="utf-8") != content:
            # Älterer Bericht vorhanden: create_file überschreibt nie; Aktualisierung als belegter
            # Patch (Sicherung durch die Bridge) und bytegenaues Zurücklesen.
            old = existing.read_text(encoding="utf-8")
            patch = invoke({"operation": "submit", "task": "patch_file",
                            "params": {"path": report_req["path"], "old_text": old, "new_text": content}})
            read = invoke({"operation": "submit", "task": "read_file",
                           "params": {"path": report_req["path"]}}) if patch.get("success") else {}
            back = (read.get("result") or {}).get("content") if isinstance(read.get("result"), dict) else None
            equal = back == content
            res = {"status": "PASS" if patch.get("success") and equal else "BLOCKED",
                   "task_ids": [t for t in (patch.get("task_id"), read.get("task_id")) if t],
                   "verification": [{"path": report_req["path"], "create_task_id": patch.get("task_id"),
                                     "read_task_id": read.get("task_id"), "equal": equal,
                                     "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}] if equal else [],
                   "error": None if equal else (patch.get("error_code") or "REPORT_READBACK_MISMATCH")}
        else:
            res = HermesBrain(master).apply_requirements([report_req])
        state["report"] = {"path": report_req["path"], "status": res.get("status"),
                           "task_ids": list(res.get("task_ids") or []),
                           "verification": list(res.get("verification") or []),
                           "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                           "bytes": len(content.encode("utf-8")),
                           "error": res.get("error")}
        _save_state(path, state)

    return _render(state)
