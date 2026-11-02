
"""Request/response models for Agent Bridge (Master)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TaskCreateRequest:
    task: str
    params: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    client: str | None = None
    session_id: str | None = None
    approval_id: str | None = None

    @classmethod
    def from_body(cls, body: dict) -> "TaskCreateRequest":
        task = (body.get("task") or body.get("name") or body.get("type") or "").strip()
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        if not params:
            skip = {
                "task", "name", "type", "params", "source", "client",
                "message", "text", "session_id", "approval_id",
            }
            params = {k: v for k, v in body.items() if k not in skip}
        return cls(
            task=task,
            params=params,
            source=(str(body["source"]) if body.get("source") is not None else None),
            client=(str(body["client"]) if body.get("client") is not None else None),
            session_id=(str(body["session_id"]) if body.get("session_id") is not None else None),
            approval_id=(str(body["approval_id"]) if body.get("approval_id") is not None else None),
        )


@dataclass
class TaskRecord:
    id: str
    task: str
    status: str  # queued|running|succeeded|failed|cancelled|awaiting_approval
    permission: str
    created_at: str
    updated_at: str
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    log: list[str] = field(default_factory=list)
    source: str | None = None
    client: str | None = None
    session_id: str | None = None
    approval_id: str | None = None

    execution: dict[str, Any] | None = None

    def to_dict(self, include_log: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_log:
            d.pop("log", None)
        return d


@dataclass
class MessageRequest:
    message: str
    source: str | None = None
    session_id: str | None = None
    client: str | None = None

    @classmethod
    def from_body(cls, body: dict) -> "MessageRequest":
        msg = body.get("message") or body.get("text") or ""
        return cls(
            message=str(msg).strip(),
            source=(str(body["source"]) if body.get("source") is not None else None),
            session_id=(str(body["session_id"]) if body.get("session_id") is not None else None),
            client=(str(body["client"]) if body.get("client") is not None else None),
        )
