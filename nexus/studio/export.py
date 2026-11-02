"""NEXUS Studio Export (Seed 2026-10-29, ST08). Nur lokal, nur G:, 0 EUR.
Leitbild: Original bleibt unveraendert (neue Version), ehrliche Fehler, KI-Inhalte kennzeichnen."""
from __future__ import annotations

import subprocess
from pathlib import Path

def export_version(src, dest_dir) -> dict:
    """Kopiert eine Version (Datei) nach dest_dir (z. B. NEXUS-All-Bibliothek), nie auf C:.
    Original bleibt unveraendert; Namenskonflikt -> Suffix _1, _2. Rueckgabe {"success", "out"}."""
    raise RuntimeError("NOT_IMPLEMENTED:export_version")
