"""Echte System-Metriken — delegiert an SystemMonitor (Backward-Compat)."""
from __future__ import annotations

from typing import Any

from nexus.computer.monitor import SystemMonitor, collect as monitor_collect, summary_de as monitor_summary_de


def metrics() -> dict[str, Any]:
    """Slim-View der Monitor-Daten (cpu/ram/disk/gpu/platform) — API-kompatibel."""
    full = monitor_collect(write_history=True)
    return {
        "success": True,
        "timestamp": full.get("timestamp"),
        "cpu": full.get("cpu"),
        "ram": full.get("ram"),
        "disk": full.get("disk"),
        "disks": full.get("disks"),
        "gpu": full.get("gpu"),
        "network": full.get("network"),
        "processes": full.get("processes"),
        "ollama": full.get("ollama"),
        "nexus": full.get("nexus"),
        "uptime": full.get("uptime"),
        "alerts": full.get("alerts"),
        "platform": full.get("platform"),
        "psutil": full.get("psutil"),
    }


def summary_de(m: dict[str, Any] | None = None) -> str:
    """Kurze deutsche Zusammenfassung für Chat/Status."""
    return monitor_summary_de(m)


class SystemMetrics:
    """Dünner Wrapper — collect() via SystemMonitor."""

    def __init__(self) -> None:
        self._mon = SystemMonitor()

    def collect(self) -> dict[str, Any]:
        return self._mon.collect()

    def summary(self) -> str:
        return self._mon.summary_de()

    def answer_query(self, q: str) -> str:
        return self._mon.answer_query(q)


__all__ = ["metrics", "summary_de", "SystemMetrics"]
