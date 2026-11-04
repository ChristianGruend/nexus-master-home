import json

import pytest

from nexus.integrations import aether_supervisor as S
from nexus.safety.budget import Budget, BudgetExceeded


class FakeLLM:
    def __init__(self, seq):
        self.seq = list(seq)

    def json(self, system, user, schema):
        return self.seq.pop(0)


def _tool(name, fn):
    return S.ToolDef(fn=fn, level="READ_ONLY", desc=name)


def test_gateway_invokes_tool_without_run():
    calls = []
    gw = S.ToolGateway(tools={"t": _tool("t", lambda value=1: calls.append(value) or {"ok": True})})
    assert gw.invoke("w1", "fileops", "t", {"value": 7}) == {"ok": True}
    assert calls == [7]


def test_gateway_budget_blocks_tool():
    gw = S.ToolGateway(Budget(tool_calls=0), {"t": _tool("t", lambda: {"ok": True})})
    with pytest.raises(BudgetExceeded, match="tool_calls"):
        gw.invoke("w1", "fileops", "t", {})


def test_gateway_ownership_conflict(monkeypatch, tmp_path):
    sb = tmp_path / "sandbox"
    sb.mkdir()
    monkeypatch.setattr(S, "WORK", tmp_path / "aether")
    monkeypatch.setattr(S, "SANDBOX", sb)
    monkeypatch.setenv("NEXUS_SWARM", "1")
    monkeypatch.setattr(S, "LOG", tmp_path / "aether.jsonl")
    gw = S.ToolGateway(tools={"sandbox_write": S.TOOLS["sandbox_write"]})
    gw.invoke("w1", "fileops", "sandbox_write", {"path": "a.txt", "content": "x"}, run_id="RUN-9")
    with pytest.raises(S.PolicyDenied, match="OWNERSHIP_CONFLICT"):
        gw.invoke("w2", "fileops", "sandbox_write", {"path": "a.txt", "content": "y"}, run_id="RUN-9")
    assert (sb / "a.txt").read_text() == "x"


def test_worker_uses_gateway(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_SWARM", "1")
    monkeypatch.setenv("NEXUS_TOOL_GATEWAY", "1")
    monkeypatch.setattr(S, "LOG", tmp_path / "aether.jsonl")
    calls = []

    class Recorder:
        def __init__(self, *a, **kw):
            pass

        def invoke(self, agent, role, tool, args, run_id=None):
            calls.append((tool, args, run_id))
            return {"items": []}

    monkeypatch.setattr(S, "ToolGateway", Recorder)
    llm = FakeLLM([{"tool": "list_files", "args": {"path": "."}, "answer": ""},
                   {"tool": "done", "args": {}, "answer": "ok"}])
    S.AgentFactory(llm).spawn("researcher").run("test", run_id="RUN-X")
    assert calls == [("list_files", {"path": "."}, "RUN-X")]


def test_supervisor_budget_abort(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_SWARM", "1")
    monkeypatch.setattr(S, "LOG", tmp_path / "aether.jsonl")
    monkeypatch.setattr(S, "WORK", tmp_path / "aether")
    llm = FakeLLM([{"action": "spawn", "template": "researcher", "agent": "", "task": "t", "answer": ""},
                   {"tool": "done", "args": {}, "answer": "ok"}])
    b = Budget(model_calls=1, max_agents=4)
    r = S.run_supervisor("frage", llm=llm, budget=b, max_steps=3)
    assert r["error"] == "BUDGET_EXCEEDED"
    assert r["budget"]["model_calls"] == 1 and r["budget"]["model_calls_used"] >= 2
