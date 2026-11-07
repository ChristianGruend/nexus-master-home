"""Phase 2 (02.11.2026): zentrale Safety-Fassade.

Eine Stelle fuer Rechte-Entscheidungen. Delegiert unveraendert an
bridge.permissions (Task-Stufen) und terminal.guard (Shell-Klassifikation,
Freigaben, Audit).
Kein Off-Schalter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexus.bridge.permissions import TASK_PERMISSIONS, PermissionLevel, allows, process_level
from nexus.policy import engine


def enabled() -> bool:
    """Immer aktiv (Off-Schalter NEXUS_PHASE2_GATE entfernt)."""
    return True


@dataclass
class Verdict:
    allowed: bool
    level_required: str
    level_granted: str
    reason: str = ""
    extra: dict = field(default_factory=dict)


def allows_level(required: PermissionLevel, granted: PermissionLevel | None = None) -> bool:
    """Identisch zu permissions.allows (eine Stufenquelle)."""
    return allows(required, granted)


def to_level(name: str) -> PermissionLevel:
    return PermissionLevel[str(name).strip().upper()]


def check(agent: str, action: str, params: dict[str, Any] | None = None,
          granted: PermissionLevel | None = None) -> Verdict:
    params = params or {}
    if action == "shell_run":
        from nexus.terminal import guard
        lvl, why = guard.required_level_shell(str(params.get("command", "")), str(params.get("shell", "session")))
        ag = guard.agent_level(agent)
        return Verdict(engine.allows(to_level(lvl), to_level(ag)), lvl, ag, why)
    req = TASK_PERMISSIONS.get(action)
    if req is None:
        return Verdict(False, "UNKNOWN", (granted or process_level()).value, "UNKNOWN_TASK")
    g = granted or process_level()
    return Verdict(allows_level(req, g), req.value, g.value, "task_permissions")


def authorize_shell(agent: str, command: str, shell: str = "session", approval_id: str | None = None, **kw) -> dict:
    """Shell-Freigabe: Gate delegiert an terminal.guard.authorize (Freigabe/Audit unveraendert)."""
    from nexus.terminal import guard
    return guard.authorize(agent, command, shell, approval_id, **kw)
