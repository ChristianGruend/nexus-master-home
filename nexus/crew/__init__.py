"""NEXUS Crew — Spezial-Agenten im Master (Coder, Scout, Fixer, ...). Orchestrator: NEXUS Master Home."""
from __future__ import annotations

from nexus.crew.roster import AGENTS, get_agent, list_public_agents, resolve_agent

__all__ = [
    "AGENTS",
    "CrewService",
    "get_agent",
    "list_public_agents",
    "resolve_agent",
    "inventory",
    "ORCHESTRATOR_ID",
]


def __getattr__(name: str):
    if name == "CrewService":
        from nexus.crew.runner import CrewService
        return CrewService
    if name == "inventory":
        from nexus.crew.capabilities import inventory
        return inventory
    if name == "ORCHESTRATOR_ID":
        from nexus.crew.capabilities import ORCHESTRATOR_ID
        return ORCHESTRATOR_ID
    raise AttributeError(name)
