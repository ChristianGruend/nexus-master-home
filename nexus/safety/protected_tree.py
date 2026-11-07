"""Stufe 2.1b: Hilfen fuer Verzeichnisbaeume auf Basis von nexus.safety.protected.

Agent-Werkzeuge, die ganze Baeume verschieben/kopieren/loeschen/durchsuchen,
duerfen keine geschuetzten Eintraege treffen. `protected` wird bewusst erst
beim Aufruf importiert, damit sich die API in Tests ersetzen laesst.
"""
from __future__ import annotations

import os
from pathlib import Path


def _is_protected(p) -> bool:
    from nexus.safety import protected
    return bool(protected.is_protected(p))


def tree_has_protected(path) -> bool:
    """True, wenn `path` selbst oder irgendein Eintrag darunter geschuetzt ist.

    Symlinks werden nicht verfolgt (followlinks=False); ein Symlink-Eintrag selbst
    wird aber per realpath geprueft (is_protected loest auf).
    """
    root = Path(path)
    if _is_protected(root):
        return True
    if not root.is_dir() or root.is_symlink():
        return False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in list(dirnames) + list(filenames):
            if _is_protected(os.path.join(dirpath, name)):
                return True
    return False


def walk_unprotected(path):
    """os.walk ohne geschuetzte Verzeichnisse/Dateien (steigt nicht hinein)."""
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = [d for d in dirnames if not _is_protected(os.path.join(dirpath, d))]
        filenames = [f for f in filenames if not _is_protected(os.path.join(dirpath, f))]
        yield dirpath, dirnames, filenames
