"""Zustandsdateien unter Gleichzeitigkeit, Lauf-Sperre und ehrliche Wiederaufnahme (2026-10-12)."""
import json
import threading

import pytest

from nexus.common.atomic import atomic_write_json, file_lock


def test_concurrent_atomic_writes_never_corrupt(tmp_path):
    target = tmp_path / "room.json"
    errors = []

    def writer(n):
        try:
            for i in range(40):
                atomic_write_json(target, {"writer": n, "i": i, "pad": "x" * 2000})
        except Exception as exc:  # pragma: no cover - Fehler wird unten geprüft
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["i"] == 39
    assert not list(tmp_path.glob("*.tmp")), "keine verwaisten temporären Dateien"


def test_file_lock_serializes_read_modify_write(tmp_path):
    target = tmp_path / "counter.json"
    atomic_write_json(target, {"n": 0})

    def inc():
        for _ in range(25):
            with file_lock(target):
                n = json.loads(target.read_text(encoding="utf-8"))["n"]
                atomic_write_json(target, {"n": n + 1})

    threads = [threading.Thread(target=inc) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert json.loads(target.read_text(encoding="utf-8"))["n"] == 100


def test_file_lock_is_reentrant_and_times_out_for_others(tmp_path):
    target = tmp_path / "order.run"
    with file_lock(target):
        with file_lock(target):  # gleicher Thread: kein Selbst-Deadlock
            pass
        result = []

        def other():
            try:
                with file_lock(target, timeout=0.2):
                    result.append("acquired")
            except TimeoutError:
                result.append("timeout")

        t = threading.Thread(target=other)
        t.start()
        t.join()
        assert result == ["timeout"]


def test_shared_room_parallel_appends_keep_all_messages(tmp_path, monkeypatch):
    from nexus.chat import shared_room
    monkeypatch.setattr(shared_room, "SHARED_DIR", tmp_path)
    room = shared_room.SharedRoom()
    errors = []

    def post(n):
        try:
            for i in range(10):
                res = room.append(session_id="S1", sender_type="human", sender_name="EDDY",
                                  content=f"m{n}-{i}", source="test")
                assert res["success"]
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=post, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(room.load("S1")["messages"]) == 50


def _web_state(**extra):
    base = {"schema": "nexus.hermes-web-order.v1", "goal": "g", "plan_id": "WEB-x", "status": "PASS",
            "model_calls": 0, "plan": {"title": "T", "slug": "t"},
            "writes": {"status": "PASS", "task_ids": ["W1", "R1", "W2", "R2"],
                       "verification": [{"path": "a/index.html", "create_task_id": "W1", "read_task_id": "R1"},
                                        {"path": "a/game.js", "create_task_id": "W2", "read_task_id": "R2"}]},
            "rounds": [], "publish": None}
    base.update(extra)
    return base


def test_stale_pass_without_current_evidence_is_not_reported_as_pass():
    from nexus.master import web_order
    state = _web_state(rounds=[{"round": 0, "patch": None,
                                "check": {"task_id": "C1", "passed": False, "criteria": {"x": False},
                                          "criteria_version": 3}}])
    out = web_order._render(state)
    assert out["status"] == "PARTIAL" and not out["success"]


def test_evidence_ids_are_tracked_per_file():
    from nexus.master import web_order
    rounds = [{"round": 0, "patch": None, "check": {"task_id": "C0", "passed": False}},
              {"round": 1, "patch": {"patch_task_id": "P1", "read_task_id": "Q1", "method": "hermes_model"},
               "check": {"task_id": "C1", "passed": False}},
              {"round": 2, "patch": {"patch_task_id": "P2", "read_task_id": "Q2", "path": "index.html",
                                     "method": "external_agent:claude_code"},
               "check": {"task_id": "C2", "passed": True, "criteria_version": 3}}]
    final, superseded = web_order._evidence_ids(_web_state(rounds=rounds, publish={"task_id": "PUB"}))
    assert set(final) == {"P1", "Q1", "P2", "Q2", "C2", "PUB"}
    assert {"W1", "R1", "W2", "R2", "C0", "C1"} <= set(superseded)


def test_external_fixes_do_not_consume_hermes_round_budget():
    from nexus.master import web_order
    rounds = [{"patch": {"method": "hermes_model"}}, {"patch": {"method": "external_agent:claude_code"}},
              {"patch": None}]
    assert web_order._stage_rounds({"rounds": rounds, "round_base": 0}) == 1


def test_superseded_check_is_explained():
    from nexus.master import web_order
    rounds = [{"round": 0, "patch": None,
               "check": {"task_id": "C0", "passed": False, "criteria_version": 2, "passed_under_version": 2,
                         "superseded_reason": "criteria_version_outdated", "criteria": {}}}]
    reply = web_order._render(_web_state(rounds=rounds))["reply"]
    assert "BESTANDEN nach Kriterien v2" in reply and "überholt" in reply


def test_refused_run_is_retried_but_executed_failure_is_not():
    from nexus.master.code_order import _run_refused
    refused = {"status": "failed", "error": "ACTION_DENIED",
               "result": {"success": False, "message": "code execution refused: CODE_EXECUTION_DISABLED"}}
    executed = {"status": "failed", "error": "TASK_FAILED",
                "result": {"returncode": 1, "containment": "windows_appcontainer"}}
    assert _run_refused(refused) is True
    assert _run_refused(executed) is False


def test_web_order_second_run_is_blocked_while_first_holds_lock(tmp_path):
    from types import SimpleNamespace
    from nexus.master import web_order
    goal = "Baut ein Testspiel im Browser"
    path = web_order._state_path(tmp_path, goal)
    master = SimpleNamespace(project_fs=SimpleNamespace(root=tmp_path), llm=None)
    result = {}

    def second():
        result["r"] = web_order.run(master, goal, {})

    with file_lock(path.with_suffix(".run")):
        t = threading.Thread(target=second)
        t.start()
        t.join()
    assert result["r"].get("error") == "WEB_ORDER_ALREADY_RUNNING"
    assert not path.exists(), "zweiter Lauf schreibt keinen Zustand"


TRACE = ('Traceback (most recent call last):\n'
         '  File "G:/w/runs/RUN-A/test_spiel.py", line 34, in <module>\n'
         '  File "G:/w/runs/RUN-A/spiel.py", line 11, in main\n'
         'EOFError: EOF when reading a line')


def test_error_signature_ignores_run_directory():
    from nexus.master.code_order import _error_signature
    assert _error_signature(TRACE) == _error_signature(TRACE.replace("RUN-A", "RUN-B"))
    assert _error_signature(TRACE) != _error_signature(TRACE.replace("line 11", "line 12"))


def test_fix_target_switches_when_same_error_persists(tmp_path):
    from types import SimpleNamespace
    from nexus.master import code_order
    proj = tmp_path / "workspace/projects/hermes/code/p"
    proj.mkdir(parents=True)
    (proj / "spiel.py").write_text("def main():\n    input()\n", encoding="utf-8")
    (proj / "test_spiel.py").write_text("import spiel\nspiel.main()\n", encoding="utf-8")
    main, test = "workspace/projects/hermes/code/p/spiel.py", "workspace/projects/hermes/code/p/test_spiel.py"
    state = {"goal": "g", "test_path": test, "files": [{"path": main}, {"path": test}],
             "run": {"result": {"stderr": TRACE}},
             "fixes": [{"ok": True, "path": main, "signature": code_order._error_signature(TRACE)}]}
    seen = {}

    class LLM:
        def chat(self, messages, **kw):
            seen["prompt"] = messages[1]["content"]
            return {"success": False}

    out = code_order._fix_round(SimpleNamespace(llm=LLM()), state, tmp_path)
    assert out["path"] == test
    assert "File to fix: test_spiel.py" in seen["prompt"]


def test_import_repair_adds_missing_import_from_sibling_module(tmp_path):
    from nexus.master.code_order import _import_repair
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "spiel.py").write_text("def compare_numbers(s, g):\n    return 'x'\n", encoding="utf-8")
    test = "import random\n\ndef test_a():\n    assert compare_numbers(1, 2)\n"
    names = {"spiel.py": "p/spiel.py", "test_spiel.py": "p/test_spiel.py"}
    out = _import_repair("NameError: name 'compare_numbers' is not defined", "p/test_spiel.py", test, names, tmp_path)
    assert out.splitlines()[:2] == ["import random", "from spiel import compare_numbers"]
    assert _import_repair("NameError: name 'unknown' is not defined", "p/test_spiel.py", test, names, tmp_path) is None


def test_vacuous_test_module_is_not_meaningful():
    from nexus.master.code_order import test_meaningful, _aggregate
    never_called = "from spiel import f\ndef test_a():\n    assert f()\n"
    no_import = "def test_a():\n    assert 1\nif __name__ == '__main__':\n    test_a()\n"
    good = "from spiel import f\ndef test_a():\n    assert f()\nif __name__ == '__main__':\n    test_a()\n"
    assert test_meaningful(never_called, ["spiel"])
    assert test_meaningful(no_import, ["spiel"])
    assert test_meaningful(good, ["spiel"]) is None
    state = {"batches": [{"status": "PASS"}]}
    assert _aggregate(state, True, True, {"status": "succeeded", "meaningful": False}) == "PARTIAL"
    assert _aggregate(state, True, True, {"status": "succeeded", "meaningful": True}) == "PASS"


def test_apply_block_whitespace_tolerant_but_unique():
    from nexus.master.nexus_order import _apply_block
    src = "def f():\n    a = 1\n    b = 2\n    return a\n"
    out = _apply_block(src, "a = 1\nb = 2", "a = 1\nb = 3")
    assert out == "def f():\n    a = 1\n    b = 3\n    return a\n"
    assert _apply_block("x = 1\nx = 1\n", "x = 1", "x = 2") is None  # mehrdeutig
    assert _apply_block(src, "c = 9", "c = 1") is None  # fehlt
