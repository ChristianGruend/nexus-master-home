"""Einzige harte Regel (Eddie, 28.10.2026): C: bleibt tabu. Technisch erzwungen fuer alle Autobuild-Schreibzugriffe."""
from __future__ import annotations

import os
from pathlib import Path

from nexus.common.paths import ROOT


from nexus.safety.paths import CDriveBlocked  # Phase 2: eine Quelle (Shim)


def check_write(path) -> Path:
    """Shim: Signatur unveraendert, Pruefung in nexus.safety.paths."""
    from nexus.safety.paths import check_write as _cw
    return _cw(path, "autobuild")


def child_env() -> dict:
    """Temp/Cache fuer Test-Subprozesse auf G: umlenken (nichts Neues auf C:)."""
    env = dict(os.environ)
    tmp = ROOT / "workspace" / "autobuild" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env.update(TEMP=str(tmp), TMP=str(tmp), PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX=str(tmp / "pycache"))
    return env
