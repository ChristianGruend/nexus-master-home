"""Stufe 2.1c: Policy-Engine (Stufen, Registry, decide) + Delegation von swarm/hermes an terminal.guard."""
import json

import pytest

from nexus.bridge.permissions import TASK_PERMISSIONS, PermissionLevel, allows as bridge_allows
from nexus.policy import engine, targets


# --- Stufenachse ---------------------------------------------------------------
def test_rank_and_allows():
    assert engine.LEVELS == ("READ_ONLY", "SAFE_WRITE", "PRIVILEGED")
    assert [engine.rank(l) for l in engine.LEVELS] == [0, 1, 2]
    for i, req in enumerate(engine.LEVELS):
        for j, got in enumerate(engine.LEVELS):
            assert engine.allows(req, got) is (j >= i)


def test_normalize_accepts_enum_and_case():
    assert engine.normalize(PermissionLevel.SAFE_WRITE) == "SAFE_WRITE"
    assert engine.normalize(" safe_write ") == "SAFE_WRITE"
    assert engine.allows(PermissionLevel.READ_ONLY, "safe_write")


def test_fail_closed_on_unknown():
    assert engine.rank("BOGUS") == engine.RANK["PRIVILEGED"]
    assert engine.rank("BOGUS", default=0) == 0
    assert not engine.allows("BOGUS", "SAFE_WRITE")      # unbekannt gefordert = PRIVILEGED
    assert not engine.allows("READ_ONLY", "BOGUS")       # unbekannt gewaehrt = nichts
    assert not engine.allows("READ_ONLY", None)


def test_bridge_allows_delegates_same_semantics():
    L = PermissionLevel
    for req in L:
        for got in L:
            assert bridge_allows(req, got) is (engine.RANK[got.value] >= engine.RANK[req.value])


# --- Registry ------------------------------------------------------------------
def test_registry_contains_all_bridge_tasks_with_same_level():
    reg = engine.registry()
    for name, lvl in TASK_PERMISSIONS.items():
        assert name in reg
        # hoehere Stufe aus anderer Quelle darf gewinnen, niedrigere nie
        assert engine.rank(reg[name].level) >= engine.rank(lvl.value)
        if reg[name].source == "bridge":
            assert reg[name].level == lvl.value


def test_registry_bridge_levels_identical_where_no_conflict():
    reg = engine.registry()
    same = [n for n, l in TASK_PERMISSIONS.items() if reg[n].level == l.value]
    assert len(same) >= len(TASK_PERMISSIONS) - 3


def test_every_tool_has_valid_target_level_source():
    reg = engine.registry()
    assert reg
    for t in reg.values():
        assert t.target in engine.TARGETS
        assert t.level in engine.LEVELS
        assert t.source in engine.SOURCES


def test_registry_examples():
    reg = engine.registry()
    assert (reg["read_file"].target, reg["read_file"].level) == ("runner", "READ_ONLY")
    assert (reg["service_restart"].target, reg["service_restart"].level) == ("host", "PRIVILEGED")
    assert reg["android_status"].target == "host"
    assert (reg["git_push"].target, reg["git_push"].level) == ("runner", "PRIVILEGED")
    assert reg["mouse_move"].target == "host"
    assert reg["web_search"].target == "core" and reg["system_status"].target == "core"
    assert reg["powershell"].level == "PRIVILEGED"   # ELEVATED_LOCAL nur in der Registry abgebildet
    assert reg["clipboard"].target == "host" and reg["screenshot"].target == "host"


def test_crew_module_keeps_elevated_local():
    from nexus.crew import capabilities as caps
    assert caps.TOOL_CAPABILITY["powershell"] == caps.ELEVATED_LOCAL
    assert caps._LEVEL_ORDER[caps.SAFE_WRITE] < caps._LEVEL_ORDER[caps.ELEVATED_LOCAL] < caps._LEVEL_ORDER[caps.PRIVILEGED]


def test_target_rules():
    assert targets.target_for("android_foo") == "host"
    assert targets.target_for("keyboard_x") == "host"
    assert targets.target_for("git_whatever") == "runner"
    assert targets.target_for("python_run_x") == "runner"
    assert targets.target_for("some_file") == "runner"
    assert targets.target_for("chat_foo") == "core"


# --- decide --------------------------------------------------------------------
def test_decide(monkeypatch):
    monkeypatch.setattr(engine, "agent_level", lambda a: {"Bob": "SAFE_WRITE"}.get(a, "READ_ONLY"))
    v = engine.decide("Bob", "read_file")
    assert v.allowed and not v.needs_approval and v.level == "READ_ONLY" and v.target == "runner"
    v = engine.decide("Bob", "git_push")
    assert not v.allowed and v.needs_approval and v.level == "PRIVILEGED"
    v = engine.decide("Bob", "gibt_es_nicht")
    assert not v.allowed and v.reason == "UNKNOWN_TOOL"
    v = engine.decide("Unbekannt", "write_file")           # unbekannter Agent = READ_ONLY
    assert not v.allowed and v.reason == "LEVEL_DENIED"
    assert engine.decide("Unbekannt", "read_file").allowed
    assert engine.decide("Unbekannt", "write_file", granted="SAFE_WRITE").allowed


# --- Delegation an terminal.guard ------------------------------------------------
@pytest.fixture
def term(tmp_path, monkeypatch):
    from nexus.terminal import service
    monkeypatch.setattr(service, "TERM_DIR", tmp_path)
    return tmp_path


def test_swarm_agent_level_delegates_to_guard(term):
    from nexus.swarm import core
    from nexus.terminal import guard
    (term / "permissions.json").write_text(
        json.dumps({"agents": {"Gast": "READ_ONLY", "Chef": "PRIVILEGED"}}), encoding="utf-8-sig")  # mit BOM
    for a in ("Gast", "Chef", "NEXUS", "Niemand"):
        assert core.agent_level(a) == guard.agent_level(a) == engine.agent_level(a)
    assert core.agent_level("Chef") == "PRIVILEGED" and core.agent_level("Niemand") == "READ_ONLY"
    assert not hasattr(core, "PERMS")


# --- hermes_tools: Freigabe-Pfad (Bugfix request_action -> request_action_approval) ----
def test_hermes_project_write_approval_roundtrip(term, monkeypatch):
    import nexus.integrations.hermes_tools as ht
    from nexus.terminal import guard
    root = term / "root"
    (root / "nexus").mkdir(parents=True)
    monkeypatch.setattr(ht, "ROOT", root)
    monkeypatch.setattr(ht, "WS", root / "workspace")
    args = {"path": "nexus/neu_datei.txt", "content": "hallo", "reason": "Test"}
    out = json.loads(ht.run_tool("project_write_request", args, root / "workspace"))
    assert out["status"] == "APPROVAL_REQUIRED" and out["approval_id"].startswith("apr_")
    with pytest.raises(PermissionError, match="PROJECT_WRITE_NOT_APPROVED"):
        ht.run_tool("project_write_file", {**args, "approval_id": out["approval_id"]}, root / "workspace")
    assert guard.decide(out["approval_id"], True)["state"] == "approved"
    res = ht.run_tool("project_write_file", {**args, "approval_id": out["approval_id"]}, root / "workspace")
    assert (root / "nexus" / "neu_datei.txt").read_text(encoding="utf-8") == "hallo"
    assert res
