"""Shim (Phase 2, 02.11.2026): statischer Plan liegt jetzt in nexus.tasks.goal_planner.static_plan."""
from nexus.tasks.goal_planner import static_plan as plan  # noqa: F401

__all__ = ["plan"]
