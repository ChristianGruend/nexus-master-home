"""Allowlist im Autobuild-Zyklus (Stufe 2.4): vor dem Schreiben und vor dem Commit; Tests laufen im Runner."""
import json

from nexus.autobuild import loop
from tests._ab_env import git, make_env

FILES = {"nexus/mod.py": "X = 1\n", "nexus/safety/paths.py": "P = 1\n"}


def _ab(edits, runner=None, tests=None):
    return loop.Autobuild(tests_runner=runner or (lambda a, t: {"ok": True, "summary": "x"}),
                          planner=lambda i: {"mode": "patch", "result": {"edits": edits}},
                          hermes=lambda c, t: {"ok": True, "path": "p"}, busy=lambda: None)


def _branches(root):
    return git(root, "branch", "--list", "autobuild/*").strip()


def test_edit_outside_allowlist_fails_with_scope(tmp_path, monkeypatch):
    root = make_env(tmp_path, monkeypatch, FILES, [{"id": "T1", "title": "t", "status": "offen", "file": "nexus/mod.py"}])
    r = _ab([{"file": "nexus/safety/paths.py", "find": "P = 1", "replace": "P = 2"}]).cycle()
    assert r["result"] == "FEHLGESCHLAGEN" and r["reason"].startswith("SCOPE:")
    assert (root / "nexus/safety/paths.py").read_text() == "P = 1\n" and _branches(root) == ""
    assert "changed" not in r  # nichts geschrieben
    assert not (tmp_path / "shared" / "autobuild").exists() or not list((tmp_path / "shared" / "autobuild").iterdir())


def test_regression_test_path_checked(tmp_path, monkeypatch):
    root = make_env(tmp_path, monkeypatch, FILES, [{"id": "T1", "title": "t", "status": "offen", "file": "nexus/mod.py",
                                                    "auto_key": "k", "regression_test": "tests/test_evil.py"}])
    plan = {"edits": [{"file": "nexus/mod.py", "find": "X = 1", "replace": "X = 2"}], "test": {"path": "tests/test_evil.py", "content": "pass\n"}}
    ab = loop.Autobuild(tests_runner=lambda a, t: {"ok": True, "summary": "x"}, planner=lambda i: {"mode": "patch", "result": plan},
                        hermes=lambda c, t: {"ok": True}, busy=lambda: None)
    r = ab.cycle()
    assert r["reason"].startswith("SCOPE:") and _branches(root) == ""


def test_side_effect_file_in_copy_blocks_commit(tmp_path, monkeypatch):
    root = make_env(tmp_path, monkeypatch, FILES, [{"id": "T1", "title": "t", "status": "offen", "file": "nexus/mod.py"}])
    holder = {}

    def runner(args, timeout):  # "Test" legt nebenbei eine unerlaubte Datei in der Kopie an
        (holder["ab"]._root / "nexus/safety/x.py").write_text("evil = 1\n", encoding="utf-8")
        return {"ok": True, "summary": "x"}

    ab = _ab([{"file": "nexus/mod.py", "find": "X = 1", "replace": "X = 2"}], runner)
    holder["ab"] = ab
    r = ab.cycle()
    assert r["reason"].startswith("SCOPE:") and "nexus/safety/x.py" in r["reason"]
    assert _branches(root) == "" and git(root, "log", "--oneline").count("\n") == 1
    assert json.loads((tmp_path / "ab" / "backlog.json").read_text())[0]["status"] == "fehlgeschlagen"


def test_branch_bereit_item_not_picked_again(tmp_path, monkeypatch):
    make_env(tmp_path, monkeypatch, FILES, [{"id": "T1", "title": "t", "status": "branch_bereit", "file": "nexus/mod.py"}])
    assert _ab([]).cycle()["result"] == "NICHTS_OFFEN"


def test_runner_mode_runs_pytest_via_exec_stream(tmp_path, monkeypatch):
    make_env(tmp_path, monkeypatch, FILES)
    monkeypatch.setenv("NEXUS_RUNNER_SOCKET", "/run/fake.sock")
    seen = {}

    def fake_exec(**kw):
        seen.update(kw)
        yield {"t": "start", "pid": 1}
        yield {"t": "out", "text": "1 passed in 0.1s"}
        yield {"t": "end", "rc": 0}

    from nexus.runner import client
    monkeypatch.setattr(client, "exec_stream", fake_exec)
    monkeypatch.setattr(loop.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kein subprocess")))
    root = tmp_path / "shared" / "autobuild" / "cid1"
    root.mkdir(parents=True)
    res = loop._run_tests(["tests/test_x.py"], 60, root)
    assert res["ok"] and res["rc"] == 0 and res["summary"] == "1 passed in 0.1s"
    assert seen["argv"][:6] == ["python", "-B", "-m", "pytest", "-q", "-p"] and seen["argv"][-1] == "tests/test_x.py"
    assert seen["cwd"] == "autobuild/cid1" and seen["timeout"] == 60


def test_runner_mode_cwd_outside_volume(tmp_path, monkeypatch):
    make_env(tmp_path, monkeypatch, FILES)
    monkeypatch.setenv("NEXUS_RUNNER_SOCKET", "/run/fake.sock")
    res = loop._run_tests(["tests"], 60, tmp_path / "anderswo")
    assert res["ok"] is False and res["summary"] == "RUNNER_CWD"


def test_runner_mode_failure_and_error_no_local_fallback(tmp_path, monkeypatch):
    make_env(tmp_path, monkeypatch, FILES)
    monkeypatch.setenv("NEXUS_RUNNER_SOCKET", "/run/fake.sock")
    from nexus.runner import client

    def boom(**kw):
        raise client.RunnerError("weg")
        yield

    monkeypatch.setattr(client, "exec_stream", boom)
    monkeypatch.setattr(loop.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("lokal")))
    res = loop._run_tests(["tests"], 60, tmp_path / "shared" / "autobuild" / "x")
    assert res["ok"] is False and res["summary"] == "RUNNER_ERROR"
