from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime

@dataclass
class DownloadJob:
    id: str
    url: str
    status: str = "queued"  # queued|running|done|error
    path: str | None = None
    error: str | None = None
    bytes: int = 0
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)
