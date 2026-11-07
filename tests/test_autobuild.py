import json
from pathlib import Path

import pytest

from nexus.autobuild import guard, loop


def test_c_drive_blocked():
    with pytest.raises(guard.CDriveBlocked):
        guard.check_write("C:\\Windows\\x.txt")
    with pytest.raises(guard.CDriveBlocked):
        guard.check_write("c:/Users/x/AppData/Local/Temp/y.txt")
    assert guard.check_write(Path(guard.ROOT) / "workspace" / "x.txt")


@pytest.fixture()
def sandbox(monkeypatch, tmp_path):
    from tests._ab_env import make_env
    rel = "nexus/mod.py"
    root = make_env(tmp_path, monkeypatch, {rel: "X = 1\n"},
                    [{"id": "T1", "title": "t", "status": "offen", "file": rel}])
    yield tmp_path / "ab", root / rel, rel


def _ab(edits, ok=True):
    return loop.Autobuild(tests_runner=lambda a, t: {"ok": ok, "summary": "x"},
                          planner=lambda i: {"mode": "patch", "result": {"edits": edits}},
                          hermes=lambda c, t: {"ok": True, "path": "p"}, busy=lambda: None)


def test_apply_and_rollback(sandbox):
    root, target, rel = sandbox
    r = _ab([{"file": rel, "find": "X = 1", "replace": "X = 2"}], ok=False).cycle()
    assert r["result"] == "ROLLBACK" and target.read_text() == "X = 1\n"
    from tests._ab_env import git
    r = _ab([{"file": rel, "find": "X = 1", "replace": "X = 2"}], ok=True).cycle()
    # Live-Repo bleibt unberuehrt; Ergebnis ist ein Branch mit der Aenderung
    assert r["result"] == "BRANCH:autobuild/t1" and r["success"] and target.read_text() == "X = 1\n"
    assert git(target.parents[1], "status", "--porcelain") == ""
    assert git(target.parents[1], "show", "autobuild/t1:" + rel) == "X = 2\n"
    assert json.loads((root / "backlog.json").read_text())[0]["status"] == "branch_bereit"


def test_bad_patch_and_c_drive(sandbox):
    root, target, rel = sandbox
    assert _ab([{"file": rel, "find": "nope", "replace": "y"}]).cycle()["result"] == "ROLLBACK"
    (root / "backlog.json").write_text(json.dumps([{"id": "T2", "title": "t", "status": "offen", "file": rel}]), encoding="utf-8")
    r = _ab([{"file": "C:/Windows/evil.py", "find": "a", "replace": "b"}]).cycle()
    assert r["result"] != "BRANCH:autobuild/t2" and ("SCOPE" in r["reason"] or "C_DRIVE_BLOCKED" in r["reason"])


def test_paused_when_busy(sandbox):
    ab = loop.Autobuild(busy=lambda: "Gaming aktiv (x)")
    assert ab.cycle()["result"] == "PAUSIERT"


# ---- Selbstheilung / Hermes-Bericht (20261028) ----
def test_record_issue_dedup_and_recurring():
    from nexus.autobuild.loop import record_issue
    items = []
    a = record_issue(items, "Hermes-Bericht", "TOOL_OUTSIDE_AUTHORIZED_CONTRACT", "log1")
    assert a["id"] == "SB-AUTO-01" and a["status"] == "beobachtet"
    b = record_issue(items, "Hermes-Bericht", "BLOCKED TOOL_OUTSIDE_AUTHORIZED_CONTRACT", "log2")
    assert b is a and len(items) == 1 and a["count"] == 2 and a["status"] == "offen" and a["logs"] == ["log1", "log2"]
    c = record_issue(items, "Hermes-Bericht", "MAINTENANCE_ACTIVE", "x")
    assert c["id"] == "SB-AUTO-02"


def test_hermes_report_waits_on_maintenance(monkeypatch):
    from nexus.autobuild import loop
    monkeypatch.setattr(loop, "_maintenance_active", lambda: True)
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    h = loop._hermes_report("T1", "x", wait_sec=-1)
    assert h["deferred"] and h["error"] == "DEFERRED_MAINTENANCE_ACTIVE"


def test_hermes_report_uses_contract_path(monkeypatch):
    from nexus.autobuild import loop
    from nexus.integrations import hermes_brain
    seen = []
    def fake(self, reqs):
        seen.append(reqs)
        return {"status": "PASS", "task_ids": ["t1", "t2"], "verification": [{"sha256": "ab"}]}
    monkeypatch.setattr(loop, "_maintenance_active", lambda: False)
    monkeypatch.setattr(hermes_brain.HermesBrain, "apply_requirements", fake)
    h = loop._hermes_report("T2", "inhalt")
    assert h["ok"] and seen == [[{"path": "workspace/projects/hermes/autobuild_T2.md", "content": "inhalt"}]]


def test_hermes_report_no_retry_on_mismatch(monkeypatch):
    from nexus.autobuild import loop
    from nexus.integrations import hermes_brain
    calls = []
    monkeypatch.setattr(loop, "_maintenance_active", lambda: False)
    monkeypatch.setattr(hermes_brain.HermesBrain, "apply_requirements",
                        lambda self, r: calls.append(1) or {"status": "BLOCKED", "error": "CONTENT_MISMATCH_NO_OVERWRITE"})
    h = loop._hermes_report("T3", "x")
    assert not h["ok"] and len(calls) == 1

def test_record_issue_uses_specific_code():
    from nexus.autobuild.loop import record_issue
    items = []
    assert record_issue(items, "Engine PLAN_FAILED", "PLAN_FAILED:NO_JSON", "x")["auto_key"] == "Engine PLAN_FAILED:NO_JSON"


def test_locate_source_finds_error_code():
    from nexus.autobuild.loop import locate_source
    f, anchor = locate_source({"auto_key": "Engine PLAN_FAILED:NO_JSON", "logs": ["x SB03: PLAN_FAILED:NO_JSON"]})
    assert f == "nexus/autobuild/loop.py" and "NO_JSON" in anchor


def test_gpt_ask_passes_json_format(monkeypatch):
    from nexus.integrations import gpt_local_agent as g
    sent = {}
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"message": {"content": "{}"}}'
    def fake(req, timeout=None):
        sent.update(json.loads(req.data.decode("utf-8"))); return R()
    import json
    monkeypatch.setattr(g.urllib.request, "urlopen", fake)
    assert g.ask("hi", fmt="json", num_ctx=4096)["success"]
    assert sent["format"] == "json" and sent["options"]["num_ctx"] == 4096


def test_correction_round_not_counted(sandbox):
    root, target, rel = sandbox
    plans = iter([{"mode": "patch", "result": {"edits": []}},
                  {"mode": "patch", "result": {"edits": [{"file": rel, "find": "X = 1", "replace": "X = 3"}]}}])
    seen = []
    def planner(i):
        seen.append(i.get("_korrektur"))
        return next(plans)
    ab = loop.Autobuild(tests_runner=lambda a, t: {"ok": True, "summary": "x"}, planner=planner,
                        hermes=lambda c, t: {"ok": True, "path": "p"}, busy=lambda: None)
    r = ab.cycle()
    assert r["result"].startswith("BRANCH:") and "NO_EDITS" in r["korrektur"]
    assert seen[0] is None and "NO_EDITS" in seen[1][1] and "_korrektur" not in json.loads((root / "backlog.json").read_text())[0]
    assert not json.loads((root / "backlog.json").read_text())[0].get("attempts")


def test_ast_excerpt_full_function_and_imports():
    src = "import os\n\ndef a(x):\n    y = 1\n    raise ValueError('NO_JSON')\n\ndef b():\n    return a(2)\n"
    ex = loop._ast_excerpt(src, 4)
    assert "import os" in ex and "def a(x):" in ex and "raise ValueError('NO_JSON')" in ex and "def b():" in ex


def test_json_from_keeps_nested_and_rejects_garbage():
    # Schutz bestehenden Verhaltens: verschachteltes JSON vollstaendig, Muell bleibt Fehler (kein Scheinerfolg)
    r = loop._json_from('Antwort: {"edits": [{"file": "a.py", "find": "x", "replace": "y"}], "test": {"path": "t", "content": "c"}}')
    assert r["edits"][0]["replace"] == "y" and r["test"]["content"] == "c"
    with pytest.raises(ValueError):
        loop._json_from("kein json hier")


def test_compute_edits_strips_excerpt_headers(sandbox):
    root, target, rel = sandbox
    out = loop._compute_edits([{"file": rel, "find": "# --- Betroffen: X ---\nX = 1", "replace": "# --- Betroffen: X ---\nX = 5"}])
    assert list(out.values())[0] == "X = 5\n"
