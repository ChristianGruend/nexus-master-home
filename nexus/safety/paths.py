"""Phase 2 (02.11.2026): eine Pfad-Pruefung fuer Schreibzugriffe.

check_write(path, scope) -> Path oder PathBlocked.
Scopes: "autobuild" (C: tabu), "nexus_patch" (C: tabu + muss unter ROOT liegen).
"""
from __future__ import annotations

import os
from pathlib import Path

from nexus.common.paths import ROOT


class PathBlocked(PermissionError):
    pass


class CDriveBlocked(PathBlocked):
    pass


def _norm(path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    return Path(os.path.abspath(str(p)))


def is_c_drive(p: Path) -> bool:
    drive = (p.drive or "").upper()
    return drive.startswith("C:") or str(p).lower().startswith(("\\\\?\\c:", "\\\\.\\c:"))


def check_write(path, scope: str = "autobuild") -> Path:
    p = _norm(path)
    if is_c_drive(p):
        raise CDriveBlocked(f"C_DRIVE_BLOCKED: {p}")
    if scope == "nexus_patch":
        root = Path(os.path.abspath(str(ROOT)))
        if root not in p.parents:
            raise PathBlocked(f"OUTSIDE_ROOT: {p}")
    return p
