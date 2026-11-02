import json
from nexus.master import skill_order as so
from nexus.skills import registry
from nexus.integrations import hermes_agent

GOAL = "nexus und hermes: baut zusammen einen neuen NEXUS-Skill systemcheck, der RAM, freien Speicher auf C: und G:, Ollama-Status und NEXUS-Health ausgibt"


def test_plan_maps_facets():
    p = so.plan(GOAL)
    assert p["name"] == "systemcheck"
    assert [s["action"] for s in p["steps"]] == ["system_status", "llm_status", "nexus_status"]
    assert so.is_build(GOAL) and not so.is_build("wie geht es dir")


def test_run_regex():
    assert so.RUN_RE.match("mach einen systemcheck").group(1) == "systemcheck"
    assert so.RUN_RE.match("mach einen systemcheck und dann kaffee") is None


def _setup(tmp_path, monkeypatch, write_ok=True):
    monkeypatch.setattr(so, "HERMES_DIR", tmp_path / "h")
    monkeypatch.setattr(registry, "USER_SKILLS", tmp_path / "s" / "user_skills.json")
    monkeypatch.setattr(registry, "PENDING_DIR", tmp_path / "s" / "pending")
    monkeypatch.setattr(registry, "SELFBUILD_AUDIT", tmp_path / "a.jsonl")
    def fake_ask(task, workdir=None):
        if write_ok:
            spec = task.split("\n")[1]
            (so.HERMES_DIR / "systemcheck.json").write_text(spec, encoding="utf-8")
        return {"success": write_ok, "request_id": "HX-T", "model": "m",
                "data": {"execution_status": "HERMES_TOOLS_EXECUTED" if write_ok else "HERMES_NO_TOOL_CALL",
                         "tool_calls": [{"tool": "write_file", "ok": True}] if write_ok else []}}
    monkeypatch.setattr(hermes_agent, "ask", fake_ask)


def test_build_pass_and_rollback(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(registry, "run_user_skill", lambda n, p=None: {"success": True, "status": "succeeded", "order": None})
    r = so.run(None, GOAL)
    assert r["status"] == "PASS" and "systemcheck" in registry._load_user()
    _setup(tmp_path / "b", monkeypatch)
    monkeypatch.setattr(registry, "run_user_skill", lambda n, p=None: {"success": False, "status": "failed"})
    r = so.run(None, GOAL)
    assert r["status"] == "FAILED" and "systemcheck" not in registry._load_user()


def test_hermes_without_tool_is_failed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, write_ok=False)
    r = so.run(None, GOAL)
    assert r["error"] == "HERMES_ARTIFACT_INVALID" and not registry._load_user()


def test_summarize(monkeypatch):
    res = {"A": {"metrics": {"ram": {"available_gb": 1.3, "total_gb": 15.9, "percent": 91},
                             "disks": [{"mount": "C:\\", "free": 8 * 1024**3, "total": 222 * 1024**3}, {"mount": "G:\\", "free": 61 * 1024**3, "total": 446 * 1024**3}]}},
           "B": {"ollama": {"reachable": True, "models": ["x"], "preferred": "q"}}, "C": {"health": {"ok": True, "score": 100, "port": 8299}}}
    monkeypatch.setattr(so, "_task_result", lambda t: res[t])
    out = "\n".join(so.summarize([{"action": "system_status", "task_id": "A"}, {"action": "llm_status", "task_id": "B"}, {"action": "nexus_status", "task_id": "C"}]))
    assert "C: 8.0 GB frei" in out and "G: 61.0 GB frei" in out and "erreichbar" in out and "Score 100" in out
