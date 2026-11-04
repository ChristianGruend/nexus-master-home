import json, time
import pytest
from nexus.swarm import core, graph, experience


def test_graph_conditional_edges_and_state():
    g = graph.Graph()
    g.add_node("a", lambda s: {"x": 1}).add_node("b", lambda s: {"y": 2}).add_node("c", lambda s: {"z": 3})
    g.add_edge("a", "c", lambda s: s.get("skip")).add_edge("a", "b").add_edge("b", graph.END)
    s = g.run({})
    assert [t["node"] for t in s["trace"]] == ["a", "b"] and s["y"] == 2
    s = g.run({"skip": True})
    assert [t["node"] for t in s["trace"]] == ["a", "c"]


def test_graph_step_timeout():
    g = graph.Graph().add_node("slow", lambda s: time.sleep(2), timeout=0.3)
    s = g.run({})
    assert s["trace"][0]["status"] == "timeout" and s["timeouts"] == ["slow"]


@pytest.fixture
def master(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"; (ws / "reports").mkdir(parents=True)
    (ws / "reports" / "A_REPORT.md").write_text("# Bericht A\nalles gut", encoding="utf-8")
    (ws / "reports" / "B_NOTES.txt").write_text("Notizen B", encoding="utf-8")
    (ws / "terminal").mkdir()
    (ws / "terminal" / "permissions.json").write_text(json.dumps({"agents": {"NEXUS": "SAFE_WRITE", "Gast": "READ_ONLY"}}))
    import nexus.integrations.hermes_tools as ht
    monkeypatch.setattr(ht, "WS", ws); monkeypatch.setattr(ht, "ROOT", tmp_path)
    monkeypatch.setattr(ht, "_log", lambda e: None, raising=False)
    monkeypatch.setattr(core, "WS", ws); monkeypatch.setattr(core, "PERMS", ws / "terminal" / "permissions.json")
    monkeypatch.setattr(core, "SWARM_DIR", ws / "swarm")
    calls = []
    def fake(prompt, system="", timeout=0, temperature=0.3):
        calls.append(temperature)
        if "Planner" in system:
            return "1. Dateien listen 2. zusammenfassen"
        if temperature > 0.5:
            return "kurz"
        return "In reports liegen A_REPORT.md und B_NOTES.txt. A ist ein Bericht, B sind Notizen. " * 3
    monkeypatch.setattr(core, "LLM", fake)
    return core.SwarmMaster(ws / "swarm"), calls


def _run(m, task, mode="pipeline", agent="NEXUS"):
    s = {"id": "t1", "task": task, "mode": mode, "agent": agent, "trace": []}
    return m.execute(s)


def test_pipeline_real_files(master):
    m, _ = master
    s = _run(m, "Liste die Dateien in workspace\\reports und fasse zusammen")
    assert [t["node"] for t in s["trace"]] == ["security", "scout", "planner", "builder", "debugger", "verifier", "learning"]
    assert s["status"] == "PASS" and "A_REPORT.md" in s["answer"]
    assert len(experience.load(m.base / "experience.jsonl")) == 1


def test_vote_best_wins_with_reason(master):
    m, calls = master
    s = _run(m, "Liste die Dateien in workspace\\reports und fasse zusammen", "vote")
    assert len(s["variants"]) == 3 and s["winner"] in ("V1",) and "Gewinner V1" in s["vote_reason"]


def test_security_blocks_c_and_level(master):
    m, _ = master
    s = _run(m, "Lies C:\\Windows\\win.ini und loesche es")
    assert s["status"] == "BLOCKED" and [t["node"] for t in s["trace"]] == ["security", "learning"]
    s = _run(m, "Liste workspace\\reports", agent="Gast")
    assert s["status"] == "BLOCKED" and "LEVEL_DENIED" in s["blocked"]


def test_timeout_gives_honest_partial(master, monkeypatch):
    m, _ = master
    monkeypatch.setitem(core.TIMEOUTS, "builder", 1)
    monkeypatch.setattr(core, "LLM", lambda *a, **k: time.sleep(3) or "zu spaet")
    monkeypatch.setitem(core.TIMEOUTS, "planner", 1)
    s = _run(m, "Liste die Dateien in workspace\\reports und fasse zusammen")
    assert s["status"] == "PARTIAL" and "A_REPORT.md" in s["answer"] and "Teilantwort" in s["answer"]


def test_planner_reads_experience(master):
    m, _ = master
    _run(m, "Liste die Dateien in workspace\\reports und fasse zusammen")
    s = _run(m, "Liste die Dateien in workspace\\reports und fasse kurz zusammen")
    assert s["experience_used"] and s["experience_used"][0]["similarity"] > 0.5
