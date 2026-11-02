"""Phase 2 Schritt 2: gate.check entscheidet identisch zu permissions.allows / terminal.guard."""
from nexus.bridge.permissions import TASK_PERMISSIONS, PermissionLevel, allows
from nexus.safety import gate
from nexus.terminal import guard


def test_task_table_equivalence():
    for name, req in TASK_PERMISSIONS.items():
        if name == "shell_run":  # Stufe kommt aus terminal.guard (test_shell_equivalence)
            continue
        for g in PermissionLevel:
            v = gate.check("NEXUS", name, {}, granted=g)
            assert v.allowed == allows(req, g), (name, g)
            assert v.level_required == req.value


def test_unknown_task_denied():
    assert gate.check("NEXUS", "does_not_exist_xyz").allowed is False


def test_shell_equivalence():
    cmds = ["Get-ChildItem G:\\", "dir", "Set-Content x.txt 1", "Start-Process notepad",
            "schtasks /create", "type D:\\x.txt", "git status", "git push origin main", "python -c 1"]
    rank = {k: i for i, k in enumerate(guard.LEVELS)}
    for agent in ("NEXUS", "Hermes", "NEXUS-GPT"):
        for sh in ("session", "python"):
            for c in cmds:
                lvl, _ = guard.required_level_shell(c, sh)
                v = gate.check(agent, "shell_run", {"command": c, "shell": sh})
                assert v.level_required == lvl
                assert v.allowed == (rank[guard.agent_level(agent)] >= rank[lvl]), (agent, sh, c)


def test_switch(monkeypatch):
    monkeypatch.setenv("NEXUS_PHASE2_GATE", "0")
    assert gate.enabled() is False
    monkeypatch.setenv("NEXUS_PHASE2_GATE", "1")
    assert gate.enabled() is True
