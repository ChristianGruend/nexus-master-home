"""Phase 2 (02.11.2026): In-Process-Aufruf ueber die Bridge-Registry.

run_task(name, params) holt den Executor aus bridge.executors (eine Registry)
und fuehrt ihn lokal aus (rec=None = In-Process). Schalter:
NEXUS_PHASE2_REGISTRY=0 -> pc_remote/terminal nutzen wieder den Direktweg.
"""
from __future__ import annotations

import os
from typing import Any

_REG: dict | None = None


def enabled() -> bool:
    return os.environ.get("NEXUS_PHASE2_REGISTRY", "1").strip() != "0"


class _Collector:
    def __init__(self) -> None:
        self.map: dict = {}

    def register(self, name, fn) -> None:
        self.map[name] = fn


def registry() -> dict:
    global _REG
    if _REG is None:
        from nexus.bridge.executors import bind_default_executors
        c = _Collector()
        bind_default_executors(c)
        _REG = c.map
    return _REG


def run_task(name: str, params: dict[str, Any]) -> dict:
    fn = registry().get(name)
    if fn is None:
        return {"success": False, "ok": False, "error": "UNKNOWN_TASK:" + name}
    return fn(params, None)


def shell_execute(command: str, **kw) -> dict:
    """Gleiche Signatur wie terminal.service.execute; mit Schalter ueber Registry-Task shell_run."""
    if not enabled():
        from nexus.terminal import service
        return service.execute(command, **kw)
    return run_task("shell_run", {"command": command, **kw})
