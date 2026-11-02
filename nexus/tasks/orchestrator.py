"""Persistent NEXUS-only multi-step task orchestration.

Plans user work into registered Bridge tasks, executes sequentially, persists after
all state transitions, and can resume after interruption. It does not introduce a
second executor: every step delegates to the existing AutomationStore/Bridge.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.automation.routines import routines
from nexus.bridge.permissions import TASK_PERMISSIONS, PermissionLevel
from nexus.common.paths import WORKSPACE

STORE = WORKSPACE / "tasks" / "orchestrator"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe(v: str) -> str:
    return "".join(c for c in v if c.isalnum() or c in "-_")


@dataclass
class Step:
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    status: str = "pending"
    routine_id: str | None = None
    task_id: str | None = None
    error: str | None = None
    attempts: int = 0
    depends_on: list[int] = field(default_factory=list)
    repair: dict[str, Any] | None = None
    diagnosis: str | None = None


@dataclass
class WorkOrder:
    id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    steps: list[Step]
    cursor: int = 0
    result: str | None = None


class Orchestrator:
    def __init__(self) -> None:
        STORE.mkdir(parents=True, exist_ok=True)

    def _path(self, oid: str) -> Path:
        return STORE / f"{_safe(oid)}.json"

    def _save(self, order: WorkOrder) -> None:
        order.updated_at = _now()
        self._path(order.id).write_text(json.dumps(asdict(order), ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, oid: str) -> WorkOrder | None:
        p = self._path(oid)
        if not p.is_file():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        d["steps"] = [Step(**s) for s in d.get("steps", [])]
        return WorkOrder(**d)

    def create(self, title: str, steps: list[dict[str, Any]]) -> WorkOrder:
        if not steps:
            raise ValueError("at least one step required")
        parsed: list[Step] = []
        for raw in steps:
            action = str(raw.get("action") or "").strip()
            if action not in TASK_PERMISSIONS:
                raise ValueError(f"unknown registered action: {action}")
            if TASK_PERMISSIONS[action] == PermissionLevel.PRIVILEGED:
                raise ValueError(f"PRIVILEGED step requires separate explicit approval: {action}")
            deps = [int(x) for x in (raw.get("depends_on") or [])]
            if any(x < 0 or x >= len(steps) for x in deps):
                raise ValueError(f"invalid dependency for {action}: {deps}")
            repair = raw.get("repair")
            if repair is not None:
                raction = str(repair.get("action") or "").strip()
                if raction not in TASK_PERMISSIONS or TASK_PERMISSIONS[raction] == PermissionLevel.PRIVILEGED:
                    raise ValueError(f"invalid automatic repair action: {raction}")
                repair = {"action": raction, "params": dict(repair.get("params") or {})}
            parsed.append(Step(action=action, params=dict(raw.get("params") or {}), title=str(raw.get("title") or action), depends_on=deps, repair=repair))
        oid = "WO-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        order = WorkOrder(oid, title.strip() or "NEXUS work order", "planned", _now(), _now(), parsed)
        self._save(order)
        return order

    def _file_preflight(self, step: Step) -> str | None:
        """Enforce file-operation intent before dispatch; never invent missing files."""
        if step.action not in ("create_file", "patch_file"):
            return None
        raw = str(step.params.get("path") or "").strip()
        if not raw:
            return "FILE_PATH_REQUIRED: file action requires path"
        target = Path(raw)
        if not target.is_absolute():
            target = (WORKSPACE.parent / target).resolve()
        else:
            target = target.resolve()
        root = WORKSPACE.parent.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return f"FILE_PATH_OUTSIDE_PROJECT: {raw}"
        if step.action == "patch_file" and not target.is_file():
            return f"REPAIR_FILE_NOT_FOUND: patch target does not exist: {raw}"
        if step.action == "create_file" and target.exists():
            return f"CREATE_FILE_ALREADY_EXISTS: create target already exists: {raw}"
        return None

    def run(self, oid: str) -> WorkOrder:
        order = self.get(oid)
        if not order:
            raise KeyError(oid)
        if order.status == "succeeded":
            return order
        order.status = "running"
        self._save(order)
        store = routines()
        for i in range(order.cursor, len(order.steps)):
            step = order.steps[i]
            order.cursor = i
            if any(d >= i or order.steps[d].status != "succeeded" for d in step.depends_on):
                step.status = "blocked"
                step.diagnosis = f"dependency not satisfied: {step.depends_on}"
                order.status = "blocked"
                order.result = step.diagnosis
                self._save(order)
                return order
            # A persisted successful step is never executed twice.
            if step.status == "succeeded":
                order.cursor = i + 1
                self._save(order)
                continue
            preflight = self._file_preflight(step)
            if preflight:
                step.status = "failed"
                step.diagnosis = preflight
                step.error = preflight
                order.status = "failed"
                order.result = f"step {i+1} preflight: {preflight}"
                self._save(order)
                return order
            step.status = "running"
            step.error = None
            step.attempts += 1
            self._save(order)
            try:
                rec = store.create(name=f"{order.id}:{i+1} {step.title}", action=step.action, trigger="manual", params=step.params, note="NEXUS orchestrator 004A")
                step.routine_id = rec.id
                self._save(order)
                rec = store.run(rec.id)
                step.task_id = rec.last_task_id
                step.status = rec.status
                step.error = rec.error
                self._save(order)
                if rec.status != "succeeded":
                    step.diagnosis = f"{rec.error_code or 'TASK_FAILED'}: {rec.error or rec.status}"
                    # Exactly one declared SAFE repair may run; no retry loop.
                    if step.repair and step.attempts == 1:
                        rr = store.create(name=f"{order.id}:{i+1} repair", action=step.repair["action"], trigger="manual", params=step.repair["params"], note=f"004B repair for step {i+1}: {step.diagnosis}")
                        rr = store.run(rr.id)
                        if rr.status == "succeeded":
                            step.status = "pending"
                            step.error = None
                            step.diagnosis += f"; repair {step.repair['action']} succeeded"
                            self._save(order)
                            return self.run(order.id)
                        step.diagnosis += f"; repair failed: {rr.error or rr.error_code}"
                    order.status = "blocked" if rec.status == "awaiting_approval" else "failed"
                    order.result = f"step {i+1} {rec.status}: {step.diagnosis}"
                    self._save(order)
                    return order
                order.cursor = i + 1
                self._save(order)
            except Exception as exc:
                step.status = "failed"
                step.error = str(exc)
                order.status = "failed"
                order.result = f"step {i+1} exception: {exc}"
                self._save(order)
                return order
        order.status = "succeeded"
        order.result = f"{len(order.steps)} steps succeeded"
        self._save(order)
        return order

    def resume(self, oid: str) -> WorkOrder:
        order = self.get(oid)
        if not order:
            raise KeyError(oid)
        if order.cursor < len(order.steps):
            step = order.steps[order.cursor]
            # running means interruption happened after dispatch may have occurred;
            # never repeat a potentially side-effecting operation automatically.
            if step.status == "running":
                order.status = "blocked"
                order.result = "interrupted running step requires task-state reconciliation before retry"
                self._save(order)
                return order
            if step.status == "failed":
                order.status = "blocked"
                order.result = "failed step exhausted controlled repair; explicit repair required"
                self._save(order)
                return order
        return self.run(oid)
