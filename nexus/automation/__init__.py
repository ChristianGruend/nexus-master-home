"""NEXUS controlled automations — thin layer over Bridge TaskManager.
Not a second executor. Scheduler is LOCAL-ONLY (not on Funnel allowlist).
PRIVILEGED never auto-fires; always needs local /approvals.
"""
from __future__ import annotations

from nexus.automation.routines import AutomationStore, routines
from nexus.automation.scheduler import LocalScheduler, scheduler

__all__ = ["AutomationStore", "routines", "LocalScheduler", "scheduler"]
