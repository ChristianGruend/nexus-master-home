"""NEXUS Master — Controller & Decision. Lazy export avoids circular imports with crew."""
from __future__ import annotations

__all__ = ["MasterController"]


def __getattr__(name: str):
    if name == "MasterController":
        from .controller import MasterController
        return MasterController
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
