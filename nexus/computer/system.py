from __future__ import annotations
import os
import platform
import shutil

from nexus.common.paths import disk_root
from nexus.computer.metrics import metrics, summary_de


class SystemInfo:
    def status(self) -> dict:
        m = metrics()
        disk_free = None
        try:
            path = disk_root()
            disk_free = round(shutil.disk_usage(path).free / (1024**3), 2)
        except Exception:
            disk_free = None
        return {
            "success": True,
            "platform": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cwd": os.getcwd(),
            "disk_free_gb": disk_free,
            "metrics": m,
            "summary": summary_de(m),
            "reply": summary_de(m),
            "note": "Live-Metriken via nexus.computer.metrics (keine Simulation).",
        }
