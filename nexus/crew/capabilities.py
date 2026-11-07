"""Agent capability allowlist — maps crew agents to tools + permission classes.

Phase 4: Local-first. Does NOT bypass Bridge PRIVILEGED approval.
Shell/powershell/run_script are ELEVATED_LOCAL (crew-scoped), not Bridge PRIVILEGED.
Orchestrator: NEXUS Master Home. No fake agents for WEB/VOICE/UI.
"""
from __future__ import annotations

from typing import Any

from nexus.policy import engine as _engine

# Bridge-aligned permission classes (same names as nexus.bridge.permissions)
READ_ONLY = "READ_ONLY"
SAFE_WRITE = "SAFE_WRITE"
PRIVILEGED = "PRIVILEGED"
# Local crew elevation (shell) — NOT the same as Bridge PRIVILEGED tasks
ELEVATED_LOCAL = "ELEVATED_LOCAL"

ORCHESTRATOR_ID = "NEXUS Master Home"

# Master-tool → capability class (crew/master tools, not Bridge task names)
TOOL_CAPABILITY: dict[str, str] = {
    # READ_ONLY
    "read_file": READ_ONLY,
    "list_dir": READ_ONLY,
    "list_dir_os": READ_ONLY,
    "status": READ_ONLY,
    "system_monitor": READ_ONLY,
    "web_search": READ_ONLY,
    "youtube_search": READ_ONLY,
    # SAFE_WRITE
    "write_file": SAFE_WRITE,
    "python_exec": SAFE_WRITE,
    "open_url": SAFE_WRITE,
    "open_path": SAFE_WRITE,
    "launch_app": SAFE_WRITE,
    "play_media": SAFE_WRITE,
    "close_opened": SAFE_WRITE,
    "close_url": SAFE_WRITE,
    "youtube_play": SAFE_WRITE,
    "volume": SAFE_WRITE,
    # ELEVATED_LOCAL (crew may use; Bridge powershell_registered_action stays PRIVILEGED+approval)
    "powershell": ELEVATED_LOCAL,
    "shell": ELEVATED_LOCAL,
    "run_script": ELEVATED_LOCAL,
}

# Role aliases → existing crew agent id (only real agents; no invent)
ROLE_ALIASES: dict[str, str | None] = {
    "code": "coder",
    "files": "scout",
    "terminal": "fixer",
    "system": "ops",
    # No dedicated crew agent — orchestrator handles; alias resolves to None
    "web": None,
    "voice": None,
    "ui": None,
}

# Per-agent allowlist: only needed caps. max_level is advisory for Bridge alignment.
# elevated_local=True means shell/powershell/run_script permitted for that agent.
AGENT_CAPABILITIES: dict[str, dict[str, Any]] = {
    "coder": {
        "role": "CODE",
        "max_level": SAFE_WRITE,
        "elevated_local": True,
        "tools": [
            "powershell", "shell", "run_script", "python_exec",
            "read_file", "write_file", "list_dir", "status",
        ],
    },
    "scout": {
        "role": "FILES",
        "max_level": READ_ONLY,
        "elevated_local": False,
        "tools": [
            "read_file", "list_dir", "list_dir_os", "open_path", "status", "web_search",
        ],
    },
    "fixer": {
        "role": "TERMINAL",
        "max_level": SAFE_WRITE,
        "elevated_local": True,
        "tools": [
            "powershell", "shell", "run_script", "python_exec",
            "read_file", "write_file", "list_dir", "status", "system_monitor",
        ],
    },
    "media": {
        "role": "MEDIA",
        "max_level": SAFE_WRITE,
        "elevated_local": False,
        "tools": [
            "play_media", "list_dir", "list_dir_os", "open_path",
            "open_url", "launch_app", "status",
        ],
    },
    "ops": {
        "role": "SYSTEM",
        "max_level": SAFE_WRITE,
        "elevated_local": True,
        "tools": [
            "status", "system_monitor", "launch_app", "open_path", "open_url",
            "powershell", "shell", "list_dir_os",
        ],
    },
    "lens": {
        "role": "FILES",
        "max_level": SAFE_WRITE,
        "elevated_local": False,
        "tools": [
            "read_file", "write_file", "list_dir", "list_dir_os", "open_path", "status",
        ],
    },
    "rap": {
        "role": "WRITE",
        "max_level": SAFE_WRITE,
        "elevated_local": False,
        "tools": [
            "read_file", "write_file", "list_dir", "status",
        ],
    },
}

# Roles without a crew agent — controlled handoff to orchestrator
ORCHESTRATOR_ROLES: dict[str, dict[str, str]] = {
    "web": {
        "hint": "Kein Crew-Agent WEB — NEXUS Master Home nutzt web_search/open_url/youtube_* direkt.",
        "intent_hint": "research|os_open_url|youtube_search",
    },
    "voice": {
        "hint": "Kein Crew-Agent VOICE — NEXUS Master Home nutzt /api/voice/* direkt.",
        "intent_hint": "voice",
    },
    "ui": {
        "hint": "Kein Crew-Agent UI — NEXUS Master Home nutzt intent=ui direkt.",
        "intent_hint": "ui",
    },
}

# Die drei Standardstufen kommen aus der Policy-Engine; ELEVATED_LOCAL bleibt Crew-intern
# (zwischen SAFE_WRITE und PRIVILEGED), PRIVILEGED rutscht dadurch um eins nach oben.
_LEVEL_ORDER = {
    READ_ONLY: _engine.RANK[READ_ONLY],
    SAFE_WRITE: _engine.RANK[SAFE_WRITE],
    ELEVATED_LOCAL: _engine.RANK[PRIVILEGED],
    PRIVILEGED: _engine.RANK[PRIVILEGED] + 1,
}


def normalize_agent_key(raw: str | None) -> str:
    return (raw or "").strip().lower().lstrip("@")


def resolve_role_alias(raw: str | None) -> tuple[str | None, str | None]:
    """Return (agent_id|None, role_key|None). role_key set when alias used."""
    key = normalize_agent_key(raw)
    if not key:
        return None, None
    if key in ROLE_ALIASES:
        return ROLE_ALIASES[key], key
    return None, None


def get_agent_capability(agent_id: str) -> dict[str, Any] | None:
    aid = normalize_agent_key(agent_id)
    return AGENT_CAPABILITIES.get(aid)


def allowed_tools_for(agent_id: str) -> set[str]:
    cap = get_agent_capability(agent_id)
    if not cap:
        return set()
    tools = set(cap.get("tools") or [])
    if not cap.get("elevated_local"):
        tools -= {t for t, lvl in TOOL_CAPABILITY.items() if lvl == ELEVATED_LOCAL}
    return tools


def tool_level(tool_name: str) -> str:
    return TOOL_CAPABILITY.get((tool_name or "").strip().lower(), SAFE_WRITE)


def filter_tool_calls(agent_id: str, calls: list[dict]) -> tuple[list[dict], list[str]]:
    """Return (allowed_calls, denied_names)."""
    allowed = allowed_tools_for(agent_id)
    ok: list[dict] = []
    denied: list[str] = []
    for c in calls or []:
        n = (c.get("name") or "").strip().lower()
        if n in allowed:
            ok.append(c)
        else:
            denied.append(n or "?")
    return ok, denied


def orchestrator_role_error(role_key: str) -> dict[str, Any]:
    meta = ORCHESTRATOR_ROLES.get(role_key) or {
        "hint": f"Kein Crew-Agent fuer Rolle '{role_key}'.",
        "intent_hint": "chat",
    }
    return {
        "success": False,
        "ok": False,
        "status": "error",
        "error": "no_specialized_agent",
        "role": role_key.upper(),
        "orchestrator": ORCHESTRATOR_ID,
        "reply": meta["hint"],
        "intent": "crew",
        "agent": None,
        "data": {
            "use_orchestrator": True,
            "intent_hint": meta["intent_hint"],
            "invented_agent": False,
        },
    }


def inventory() -> dict[str, Any]:
    """Public inventory for capabilities endpoint / reports."""
    agents = []
    for aid, cap in AGENT_CAPABILITIES.items():
        tools = sorted(allowed_tools_for(aid))
        agents.append({
            "id": aid,
            "role": cap.get("role"),
            "max_level": cap.get("max_level"),
            "elevated_local": bool(cap.get("elevated_local")),
            "tools": tools,
            "tool_levels": {t: tool_level(t) for t in tools},
        })
    return {
        "orchestrator": ORCHESTRATOR_ID,
        "agents": agents,
        "role_aliases": {k: v for k, v in ROLE_ALIASES.items()},
        "orchestrator_roles": list(ORCHESTRATOR_ROLES.keys()),
        "permission_classes": [READ_ONLY, SAFE_WRITE, PRIVILEGED],
        "elevated_local_note": (
            "powershell/shell/run_script are ELEVATED_LOCAL for crew; "
            "Bridge PRIVILEGED tasks still require local /approvals — no bypass."
        ),
        "bridge_privileged_approval": True,
    }
