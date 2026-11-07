"""Nachschlagen echter API-Namen + Hermes-Review im Funktionsmodus (20261030_lookup)."""

import nexus.autobuild.loop as L

PIL_CODE = "from PIL import Image, ImageDraw, ImageFont, ImageOps\n"


def test_api_hints_lists_real_names():
    h = L.api_hints(PIL_CODE)
    assert "Image: " in h and "Transpose" in h and "ImageOps: " in h and "nexus" not in h


def test_api_hints_suggests_close_match_for_attributeerror():
    err = "AttributeError: type object 'Transpose' has no attribute 'FLIPPED_LEFT_RIGHT'"
    h = L.api_hints(PIL_CODE, err)
    assert "FLIPPED_LEFT_RIGHT' existiert NICHT" in h and "Image.Transpose.FLIP_LEFT_RIGHT" in h


def test_api_hints_nameerror_and_unbound():
    h = L.api_hints("import json\n", "NameError: name 'dumsp' is not defined\nUnboundLocalError: x")
    assert "json.dumps" in h and "UnboundLocalError" in h
    assert L.api_hints("from nexus.studio.core import Studio\n") == ""


def test_plan_function_prompt_contains_lookup_and_fix():
    item = {"id": "ST09", "title": "t", "file": "nexus/studio/imgtools.py", "anchor": "def tool(",
            "last_test_output": "AttributeError: type object 'Transpose' has no attribute 'FLIPPED_LEFT_RIGHT'"}
    seen = []
    L.plan_function(item, ask=lambda p: seen.append(p) or "```python\ndef tool(img, name, **p):\n    return img\n```")
    assert "VERFUEGBARE API" in seen[0] and "FLIP_LEFT_RIGHT" in seen[0]


class _Item(dict):
    pass


REL = "nexus/rv_target.py"


def _ab(tmp_reviews, planner_results, monkeypatch, tmp_path):
    from tests._ab_env import make_env
    root = make_env(tmp_path, monkeypatch, {REL: "def f():\n    raise ValueError('NOT_IMPLEMENTED')\n"})
    target = root / REL  # nur zum Nachlesen; geschrieben wird in der Arbeitskopie
    rel = REL
    item = {"id": "TST-RV", "title": "t", "file": rel, "anchor": "def f(", "status": "offen", "tests": ["x"]}
    saved = {}
    monkeypatch.setattr(L, "load_backlog", lambda: [item])
    monkeypatch.setattr(L, "save_backlog", lambda items: saved.setdefault("n", 0))
    monkeypatch.setattr(L, "_emit", lambda *a, **k: None)
    plans = iter(planner_results)
    tests = []
    ab = L.Autobuild(tests_runner=lambda a, t: tests.append((ab._root / REL).read_text(encoding="utf-8")) or {"ok": True, "summary": "1 passed"},
                     planner=lambda it: next(plans), hermes=lambda c, t: {"ok": True}, busy=lambda: None,
                     reviewer=lambda it, pl: next(tmp_reviews))
    return ab, item, target, tests, rel


def _plan(rel, ret):
    return {"mode": "patch", "fn_mode": True, "result": {"edits": [{"file": rel, "function": "f", "code": f"def f():\n    return {ret}"}]}}


def test_review_issues_trigger_nachbesserung(monkeypatch, tmp_path):
    rel = REL
    ab, item, target, tests, rel = _ab(iter([{"status": "ISSUES", "reply": "PROBLEME: return 1 falsch"}]),
                                       [_plan(rel, 1), _plan(rel, 2)], monkeypatch, tmp_path)
    rec = ab.cycle(full_suite=False)
    assert rec["result"].startswith("BRANCH:") and rec["review"]["status"] == "ISSUES" and rec["review"]["nachgebessert"]
    assert "return 2" in tests[0]


def test_review_unavailable_continues_without(monkeypatch, tmp_path):
    rel = REL
    ab, item, target, tests, rel = _ab(iter([{"status": "UNAVAILABLE", "detail": "TIMEOUT_240s"}]), [_plan(rel, 1)], monkeypatch, tmp_path)
    rec = ab.cycle(full_suite=False)
    assert rec["result"].startswith("BRANCH:") and rec["review"]["status"] == "UNAVAILABLE" and "return 1" in tests[0]


def test_hermes_review_timeout_and_unreachable(monkeypatch):
    from nexus.integrations import hermes_agent as H
    monkeypatch.setattr(H, "health", lambda: {"reachable": False, "error": "HERMES_UNAVAILABLE"})
    plan = {"result": {"edits": [{"code": "def f():\n    return 1"}]}}
    assert L.hermes_review({}, plan)["status"] == "UNAVAILABLE"
    import time
    monkeypatch.setattr(H, "health", lambda: {"reachable": True})
    monkeypatch.setattr(H, "ask", lambda t: time.sleep(3) or {"reply": "OK"})
    assert L.hermes_review({}, plan, timeout=1)["detail"] == "TIMEOUT_1s"
    monkeypatch.setattr(H, "ask", lambda t: {"reply": "OK, passt"})
    assert L.hermes_review({}, plan)["status"] == "OK"
    monkeypatch.setattr(H, "ask", lambda t: {"reply": "PROBLEME: Name falsch"})
    assert L.hermes_review({}, plan)["status"] == "ISSUES"
