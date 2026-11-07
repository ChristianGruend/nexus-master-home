"""Registrierte Entwicklungsaufgaben für Hermes (Browserprüfung, Vorschau, NEXUS-Patches).

Trennung der drei Stufen:
  1. Entwicklungsprojekt   workspace/projects/hermes/code/<projekt>/    (Hermes schreibt)
  2. Geprüfte Vorschau     workspace/projects/hermes/preview/<projekt>/ (nur nach bestandener
                           Browserprüfung mit identischen Datei-Hashes; /preview/<projekt>/)
  3. Produktive NEXUS-Integration: ausschließlich über nexus_apply_patch (PRIVILEGED, lokale
     Freigabe), nach isoliertem Test, mit Sicherung und Rückweg.

Geschützte Bereiche (Authentifizierung, Freigaben, Berechtigungen, Sandbox, Werkzeugvertrag,
Start-/Stopp-Skripte) sind für Patches gesperrt; das ist im Code fest verdrahtet.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.bridge.errors import BridgeError, INVALID_REQUEST, ACTION_DENIED
from nexus.bridge.models import TaskRecord
from nexus.common.paths import ROOT

ROOT = ROOT.resolve()
CODE_ROOT = "workspace/projects/hermes/code"
PREVIEW_ROOT = "workspace/projects/hermes/preview"
PATCH_ROOT = "workspace/projects/hermes/nexus_patches"
PATCH_ID = re.compile(r"PATCH-[0-9a-f]{12}\Z")
PROJECT = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
PREVIEW_EXT = {"html", "css", "js", "json", "md"}
PATCH_EXT = {"py", "html", "css", "js", "json", "md"}
PATCH_MAX_FILES = 5
PATCH_MAX_BYTES = 120_000
# Fest gesperrt: Sicherheitskern, Freigabeprozess, Authentifizierung, Sandbox, Werkzeugvertrag.
PROTECTED = (
    "nexus/bridge/auth.py", "nexus/bridge/approvals.py", "nexus/bridge/permissions.py",
    "nexus/bridge/tasks.py", "nexus/bridge/api.py", "nexus/bridge/appcontainer.py",
    "nexus/bridge/code_sandbox.py", "nexus/bridge/web_check.py", "nexus/bridge/dev_executors.py",
    "nexus/bridge/executors.py", "nexus/bridge/outcomes.py", "nexus/bridge/storage.py",
    "nexus/integrations/hermes_client.py", "nexus/maintenance.py", "nexus/chat/static/approvals.html",
)
PROTECTED_PREFIX = ("nexus/bridge_gateway/", "nexus/maintenance/")
PROTECTED_WORDS = ("token", "secret", "credential", "password", "approval", "auth")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _task_record(task_id: str) -> dict:
    if not isinstance(task_id, str) or not re.fullmatch(r"NX-\d{8}-\d{4,}", task_id):
        return {}
    path = ROOT / "workspace" / "bridge" / "tasks" / (task_id + ".json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) and value.get("id") == task_id else {}


# --------------------------------------------------------------------------- Web
def exec_hermes_check_web(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    from nexus.bridge.code_sandbox import SandboxError
    from nexus.bridge.web_check import check_web

    rel = str(params.get("path") or "").strip().replace("\\", "/")
    try:
        return check_web(ROOT, rel)
    except SandboxError as exc:
        raise BridgeError(ACTION_DENIED, "web check refused: " + str(exc), http_status=403) from None


def exec_hermes_publish_preview(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """Nur eine bestandene, hash-identische Browserprüfung darf in die Vorschau."""
    rel = str(params.get("path") or "").strip().replace("\\", "/")
    check_id = str(params.get("check_task_id") or "")
    check = _task_record(check_id)
    result = check.get("result") if isinstance(check.get("result"), dict) else {}
    if not (check.get("task") == "hermes_check_web" and check.get("status") == "succeeded"
            and result.get("passed") is True and result.get("path") == rel):
        raise BridgeError(ACTION_DENIED, "preview requires a passed hermes_check_web receipt for this entry",
                          http_status=403)
    project_dir = (ROOT / rel).parent
    project = project_dir.name
    if not PROJECT.match(project) or project_dir.parent != (ROOT / CODE_ROOT):
        raise BridgeError(ACTION_DENIED, "preview only for top-level code projects", http_status=403)
    expected = result.get("sha256") or {}
    current = {}
    for item in sorted(project_dir.rglob("*")):
        if item.is_file() and not item.is_symlink() and item.suffix.lower().lstrip(".") in PREVIEW_EXT:
            current[item.relative_to(project_dir).as_posix()] = _sha(item.read_bytes())
    if current != expected:
        raise BridgeError(ACTION_DENIED, "project files changed since the passed check; re-check required",
                          http_status=409)
    target = ROOT / PREVIEW_ROOT / project
    staging = ROOT / PREVIEW_ROOT / (".staging-" + project)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for name in current:
        dest = staging / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project_dir / name, dest)
    (staging / "preview.json").write_text(json.dumps({
        "project": project, "source": rel, "check_task_id": check_id, "sha256": current,
        "screenshot": result.get("screenshot"), "published_at": _now(), "publish_task_id": rec.id,
        "criteria_version": result.get("criteria_version"), "criteria": result.get("criteria"),
        "stage": "verified_preview"}, ensure_ascii=False, indent=2), encoding="utf-8")
    previous = ROOT / PREVIEW_ROOT / (".previous-" + project)
    shutil.rmtree(previous, ignore_errors=True)
    if target.exists():
        target.replace(previous)
    staging.replace(target)
    copied = {n: _sha((target / n).read_bytes()) for n in current}
    from nexus.bridge.web_check import verify_served
    from nexus.bridge.code_sandbox import SandboxError
    url = "/preview/" + project + "/" + Path(rel).name
    try:
        served = verify_served("http://127.0.0.1:8299" + url, ROOT / "workspace/projects/hermes/preview_evidence" / project)
    except SandboxError as exc:
        served = {"passed": False, "console_errors": [str(exc)]}
    if copied != current or not served.get("passed"):
        shutil.rmtree(target, ignore_errors=True)
        if previous.exists():
            previous.replace(target)
        raise BridgeError(ACTION_DENIED, "served preview failed verification: "
                          + "; ".join(served.get("console_errors") or ["hash mismatch"])[:600], http_status=409)
    return {"published": copied == current, "served_check": served, "project": project, "files": sorted(current),
            "sha256": copied, "url": url,
            "check_task_id": check_id, "stage": "verified_preview"}


# --------------------------------------------------------------------------- NEXUS-Patches
def _patch_dir(patch_id: str) -> Path:
    if not isinstance(patch_id, str) or not PATCH_ID.match(patch_id):
        raise BridgeError(INVALID_REQUEST, "invalid patch_id", http_status=400)
    return ROOT / PATCH_ROOT / patch_id


def nexus_target_allowed(path: str) -> str | None:
    """Normalisierter, patchbarer NEXUS-Pfad oder None (gesperrt/ungültig)."""
    if not isinstance(path, str):
        return None
    value = path.strip().replace("\\", "/")
    if not (value.startswith("nexus/") or value.startswith("tests/")) or ".." in value.split("/"):
        return None
    if value.rsplit(".", 1)[-1].lower() not in PATCH_EXT or "__pycache__" in value or ".bak" in value:
        return None
    low = value.lower()
    from nexus.safety.protected import is_protected  # Stufe 2.1b: zentrale Schutzliste
    if is_protected(ROOT / value):
        return None
    if value in PROTECTED or value.startswith(PROTECTED_PREFIX) or any(w in low for w in PROTECTED_WORDS):
        return None
    target = (ROOT / value).resolve()
    if ROOT not in target.parents or target.is_symlink():
        return None
    try:  # Phase 2: gemeinsame Pfad-Pruefung (C: tabu, unter ROOT)
        from nexus.safety.paths import check_write as _cw, PathBlocked as _pb
        _cw(target, "nexus_patch")
    except _pb:
        return None
    return value


def _manifest(patch_dir: Path) -> dict:
    try:
        return json.loads((patch_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def exec_hermes_prepare_nexus_patch(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """Änderung getrennt vom produktiven System vorbereiten: Original sichern, Vorschlag, Diff."""
    patch_dir = _patch_dir(params.get("patch_id"))
    goal = str(params.get("goal") or "")[:500]
    files = params.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= PATCH_MAX_FILES:
        raise BridgeError(INVALID_REQUEST, "1..5 files required", http_status=400)
    entries, diff = [], []
    for item in files:
        path = nexus_target_allowed((item or {}).get("path"))
        content = (item or {}).get("content")
        if path is None:
            raise BridgeError(ACTION_DENIED, "protected or invalid NEXUS path: " + str((item or {}).get("path")),
                              http_status=403)
        if not isinstance(content, str) or len(content.encode("utf-8")) > PATCH_MAX_BYTES:
            raise BridgeError(INVALID_REQUEST, "invalid content for " + path, http_status=400)
        source = ROOT / path
        original = source.read_bytes() if source.is_file() else None
        entries.append({"path": path, "new_file": original is None,
                        "original_sha256": _sha(original) if original is not None else None,
                        "proposed_sha256": _sha(content.encode("utf-8"))})
        old_text = original.decode("utf-8", "replace") if original is not None else ""
        diff.extend(difflib.unified_diff(old_text.splitlines(True), content.splitlines(True),
                                         "a/" + path, "b/" + path))
        if original is not None:
            (patch_dir / "original" / path).parent.mkdir(parents=True, exist_ok=True)
            (patch_dir / "original" / path).write_bytes(original)
        (patch_dir / "proposed" / path).parent.mkdir(parents=True, exist_ok=True)
        (patch_dir / "proposed" / path).write_bytes(content.encode("utf-8"))
    if not any(e["original_sha256"] != e["proposed_sha256"] for e in entries):
        raise BridgeError(INVALID_REQUEST, "patch changes nothing", http_status=400)
    diff_text = "".join(diff)
    (patch_dir / "patch.diff").write_text(diff_text, encoding="utf-8")
    manifest = {"patch_id": patch_dir.name, "goal": goal, "files": entries, "prepared_at": _now(),
                "prepare_task_id": rec.id, "status": "PREPARED", "diff_sha256": _sha(diff_text.encode("utf-8"))}
    (patch_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"prepared": True, "patch_id": patch_dir.name, "files": entries,
            "diff_lines": diff_text.count("\n"), "diff_sha256": manifest["diff_sha256"],
            "diff": diff_text[:12000], "stage": "isolated_patch"}


def _related_tests(paths: list[str]) -> list[str]:
    modules = {p[:-3].replace("/", ".") for p in paths if p.endswith(".py")}
    names = {m.rsplit(".", 1)[-1] for m in modules}
    out = []
    for test in sorted((ROOT / "tests").glob("test_*.py")):
        text = test.read_text(encoding="utf-8", errors="replace")
        if any(m in text for m in modules) or any(re.search(r"\b" + re.escape(n) + r"\b", text) for n in names):
            out.append(test.name)
    return out[:12]


_TEST_RUNNER = r'''
import json, sys, unittest, py_compile, importlib
changed = json.loads(sys.argv[1]); tests = json.loads(sys.argv[2])
report = {"compile": {}, "import": {}, "tests": {}}
for p in changed:
    if p.endswith(".py"):
        try:
            py_compile.compile(p, doraise=True); report["compile"][p] = "ok"
        except py_compile.PyCompileError as e:
            report["compile"][p] = "error: " + str(e)[:800]
sys.path.insert(0, ".")
for p in changed:
    if p.endswith(".py") and p.startswith("nexus/") and report["compile"].get(p) == "ok":
        mod = p[:-3].replace("/", ".")
        try:
            importlib.import_module(mod); report["import"][mod] = "ok"
        except BaseException as e:
            report["import"][mod] = "error: " + type(e).__name__ + ": " + str(e)[:400]
import os
if os.name == "nt":
    # Python >= 3.13 setzt bei mkdir(mode=0o700) eine Nur-Besitzer-ACL; im AppContainer ist der
    # Prozess nicht Besitzer -> WinError 5 für tmp_path/mkdtemp. Nur in diesem isolierten Lauf:
    # Modus ignorieren (alle Ordner liegen ohnehin im Laufordner des AppContainers).
    _mkdir = os.mkdir
    def _mkdir_inherit(path, mode=0o777, *, dir_fd=None):
        return _mkdir(path, dir_fd=dir_fd) if dir_fd is not None else _mkdir(path)
    os.mkdir = _mkdir_inherit
try:
    import pytest
except ImportError:
    pytest = None
import xml.etree.ElementTree as ET
for t in tests:
    if pytest is not None:
        # Repo-Tests sind pytest-Funktionen; Ergebnis aus JUnit-XML, Temp-Dateien nur im Laufordner.
        junit = "_junit_" + t + ".xml"
        try:
            pytest.main(["-q", "-p", "no:cacheprovider", "--basetemp=_pt_" + t[:-3], "--junitxml=" + junit,
                         "tests/" + t])
        except BaseException as e:  # Abbruch beim Aufräumen darf den Bericht nicht verhindern
            report.setdefault("runner_errors", []).append(t + ": " + type(e).__name__ + ": " + str(e)[:200])
        try:
            suites = ET.parse(junit).getroot()
            suites = [suites] if suites.tag == "testsuite" else list(suites)
            cases = [c for s in suites for c in s.iter("testcase")]
            fails = [c for c in cases if c.find("failure") is not None]
            errs = [c for c in cases if c.find("error") is not None]
            skipped = [c for c in cases if c.find("skipped") is not None]
            report["tests"][t] = {"run": len(cases), "failures": len(fails), "errors": len(errs),
                                  "skipped": len(skipped), "runner": "pytest",
                                  "details": [c.get("name", "") + ": " + ((c.find("failure") if c.find("failure") is not None
                                              else c.find("error")).get("message") or "")[-600:] for c in fails + errs][:5]}
        except Exception as e:
            report["tests"][t] = {"run": 0, "failures": 0, "errors": 1, "skipped": 0, "runner": "pytest",
                                  "details": ["REPORT_UNREADABLE: " + type(e).__name__]}
        continue
    # Fallback ohne pytest: tests/ ist kein Paket -> tests/ selbst als Startverzeichnis.
    suite = unittest.defaultTestLoader.discover("tests", pattern=t, top_level_dir="tests")
    res = unittest.TextTestRunner(stream=open("_t.log", "a"), verbosity=0).run(suite)
    report["tests"][t] = {"run": res.testsRun, "failures": len(res.failures), "errors": len(res.errors),
                          "skipped": len(res.skipped), "runner": "unittest",
                          "details": [str(x[0]) + ": " + x[1][-600:] for x in (res.failures + res.errors)][:5]}
print("NEXUS_PATCH_REPORT=" + json.dumps(report))
'''


def _sandbox_tests(tree_overlay: Path | None, changed: list[str], tests: list[str]) -> dict:
    from nexus.bridge import appcontainer
    run_dir = appcontainer.new_run_dir()
    try:
        for base in ("nexus", "tests"):
            for item in (ROOT / base).rglob("*"):
                if (item.is_file() and not item.is_symlink() and "__pycache__" not in item.parts
                        and ".bak" not in item.name and item.suffix.lower().lstrip(".") in PATCH_EXT):
                    dest = run_dir / item.relative_to(ROOT)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, dest)
        if tree_overlay is not None:
            for item in tree_overlay.rglob("*"):
                if item.is_file():
                    dest = run_dir / item.relative_to(tree_overlay)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, dest)
        (run_dir / "_nexus_patch_runner.py").write_text(_TEST_RUNNER, encoding="utf-8")
        import sys
        out = appcontainer.run([sys.executable, "-E", "-s", "-B", "_nexus_patch_runner.py",
                                json.dumps(changed), json.dumps(tests)], run_dir,
                               memory_mb=1024, processes=6, cpu_seconds=150, wall_seconds=180)
    finally:
        appcontainer.cleanup(run_dir)
    line = next((l for l in out["stdout"].splitlines() if l.startswith("NEXUS_PATCH_REPORT=")), None)
    report = json.loads(line.split("=", 1)[1]) if line else {}
    return {"report": report, "returncode": out["returncode"], "timed_out": out["timed_out"],
            "stderr": "\n".join(l for l in out["stderr"].splitlines()
                                if not l.startswith("Failed to find real location"))[-2000:]}


def exec_hermes_test_nexus_patch(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """Vorschlag in einer isolierten NEXUS-Kopie (AppContainer) prüfen, gegen die Ausgangslage."""
    from nexus.bridge.appcontainer import self_test
    patch_dir = _patch_dir(params.get("patch_id"))
    manifest = _manifest(patch_dir)
    if not manifest:
        raise BridgeError(INVALID_REQUEST, "patch not prepared", http_status=404)
    if not self_test().get("ok"):
        raise BridgeError(ACTION_DENIED, "isolation self-test failed; no patch test executed", http_status=403)
    changed = [f["path"] for f in manifest["files"]]
    tests = _related_tests(changed)
    baseline = _sandbox_tests(None, changed, tests)
    proposed = _sandbox_tests(patch_dir / "proposed", changed, tests)
    base_r, prop_r = baseline["report"], proposed["report"]

    def bad(rep, t):
        r = (rep.get("tests") or {}).get(t) or {}
        return (r.get("failures") or 0) + (r.get("errors") or 0)
    regressions = [t for t in tests if bad(prop_r, t) > bad(base_r, t)]
    compile_ok = all(v == "ok" for v in (prop_r.get("compile") or {}).values())
    import_ok = all(v == "ok" for v in (prop_r.get("import") or {}).values())
    # Zugehörige Tests müssen tatsächlich gelaufen sein (0 ausgeführte Tests belegen nichts).
    tests_ran = all((((prop_r.get("tests") or {}).get(t) or {}).get("run") or 0) > 0 for t in tests)
    passed = (bool(prop_r) and compile_ok and import_ok and tests_ran and not regressions
              and not proposed["timed_out"])
    result = {"patch_id": patch_dir.name, "tests": tests, "baseline": base_r, "proposed": prop_r,
              "regressions": regressions, "compile_ok": compile_ok, "import_ok": import_ok, "tests_ran": tests_ran,
              "passed": passed, "proposed_sha256": {f["path"]: f["proposed_sha256"] for f in manifest["files"]},
              "stderr": proposed["stderr"][-1500:], "containment": "windows_appcontainer"}
    (patch_dir / "test_report.json").write_text(json.dumps({**result, "test_task_id": rec.id, "at": _now()},
                                                           ensure_ascii=False, indent=2), encoding="utf-8")
    manifest.update(status="TESTED_PASS" if passed else "TESTED_FAIL", test_task_id=rec.id)
    (patch_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _approval_bound(rec: TaskRecord, patch_id: str) -> None:
    """Die verbrauchte Freigabe muss genau für diese Patch-ID erteilt worden sein."""
    from nexus.bridge.approvals import approvals
    try:
        approved = approvals().get(str(rec.approval_id or ""))
    except Exception:
        approved = None
    if approved is None or (approved.params or {}).get("patch_id") != patch_id:
        raise BridgeError(ACTION_DENIED, "approval is not bound to this patch_id", http_status=403)


def exec_nexus_apply_patch(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """PRIVILEGED: nur nach lokaler Freigabe, bestandenem Test und unveränderten Originalen."""
    patch_dir = _patch_dir(params.get("patch_id"))
    _approval_bound(rec, patch_dir.name)
    manifest = _manifest(patch_dir)
    try:
        report = json.loads((patch_dir / "test_report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    if not manifest or report.get("passed") is not True:
        raise BridgeError(ACTION_DENIED, "patch has no passed isolated test", http_status=403)
    for f in manifest["files"]:
        if nexus_target_allowed(f["path"]) is None:
            raise BridgeError(ACTION_DENIED, "protected path " + f["path"], http_status=403)
        proposed = (patch_dir / "proposed" / f["path"]).read_bytes()
        if _sha(proposed) != f["proposed_sha256"] or report["proposed_sha256"].get(f["path"]) != f["proposed_sha256"]:
            raise BridgeError(ACTION_DENIED, "proposed file differs from tested version", http_status=409)
        live = ROOT / f["path"]
        live_sha = _sha(live.read_bytes()) if live.is_file() else None
        if live_sha != f["original_sha256"]:
            raise BridgeError(ACTION_DENIED, "live file changed since preparation: " + f["path"], http_status=409)
    backup = ROOT / "workspace" / "backups" / ("nexus-patch-" + patch_dir.name + "-" + time.strftime("%Y%m%d-%H%M%S"))
    applied = []
    for f in manifest["files"]:
        live = ROOT / f["path"]
        if live.is_file():
            (backup / f["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(live, backup / f["path"])
        live.parent.mkdir(parents=True, exist_ok=True)
        tmp = live.with_name(live.name + ".patch-tmp")
        tmp.write_bytes((patch_dir / "proposed" / f["path"]).read_bytes())
        tmp.replace(live)
        applied.append(f["path"])
    problems = []
    for f in manifest["files"]:
        live = ROOT / f["path"]
        if _sha(live.read_bytes()) != f["proposed_sha256"]:
            problems.append("hash:" + f["path"])
        if f["path"].endswith(".py"):
            try:
                compile(live.read_text(encoding="utf-8"), f["path"], "exec")
            except SyntaxError as exc:
                problems.append("syntax:" + f["path"] + ":" + str(exc)[:200])
    if problems:
        _restore(manifest, backup)
        raise BridgeError(ACTION_DENIED, "applied patch failed verification and was rolled back: "
                          + "; ".join(problems), http_status=409)
    needs_restart = any(p.endswith(".py") for p in applied)
    manifest.update(status="APPLIED", apply_task_id=rec.id, backup=str(backup.relative_to(ROOT)).replace("\\", "/"),
                    applied_at=_now(), needs_restart=needs_restart)
    (patch_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"applied": True, "patch_id": patch_dir.name, "files": applied, "verified": True,
            "backup": manifest["backup"], "needs_restart": needs_restart,
            "activation": "nexus_restart (PRIVILEGED) erforderlich" if needs_restart else "statische Datei sofort aktiv"}


def _restore(manifest: dict, backup: Path) -> list[str]:
    restored = []
    for f in manifest["files"]:
        live = ROOT / f["path"]
        saved = backup / f["path"]
        if saved.is_file():
            shutil.copyfile(saved, live)
            restored.append(f["path"])
        elif f.get("new_file") and live.is_file():
            live.unlink()
            restored.append(f["path"])
    return restored


def exec_nexus_rollback_patch(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """PRIVILEGED: angewendeten Patch aus der Sicherung zurücknehmen."""
    patch_dir = _patch_dir(params.get("patch_id"))
    _approval_bound(rec, patch_dir.name)
    manifest = _manifest(patch_dir)
    if manifest.get("status") != "APPLIED" or not manifest.get("backup"):
        raise BridgeError(ACTION_DENIED, "patch is not applied", http_status=409)
    for f in manifest["files"]:
        live = ROOT / f["path"]
        if not live.is_file() or _sha(live.read_bytes()) != f["proposed_sha256"]:
            raise BridgeError(ACTION_DENIED, "live file differs from applied patch: " + f["path"], http_status=409)
    restored = _restore(manifest, ROOT / manifest["backup"])
    ok = all((not (ROOT / f["path"]).is_file() and f.get("new_file"))
             or ((ROOT / f["path"]).is_file() and _sha((ROOT / f["path"]).read_bytes()) == f["original_sha256"])
             for f in manifest["files"])
    manifest.update(status="ROLLED_BACK" if ok else "ROLLBACK_INCOMPLETE", rollback_task_id=rec.id)
    (patch_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"rolled_back": ok, "patch_id": patch_dir.name, "restored": restored}


EXECUTORS = {
    "hermes_check_web": exec_hermes_check_web,
    "hermes_publish_preview": exec_hermes_publish_preview,
    "hermes_prepare_nexus_patch": exec_hermes_prepare_nexus_patch,
    "hermes_test_nexus_patch": exec_hermes_test_nexus_patch,
    "nexus_apply_patch": exec_nexus_apply_patch,
    "nexus_rollback_patch": exec_nexus_rollback_patch,
}
