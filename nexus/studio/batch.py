"""NEXUS Studio Stapelverarbeitung (Seed 2026-10-29, ST13). Nur lokal, nur G:, 0 EUR.
Leitbild: Original bleibt unveraendert (neue Version), ehrliche Fehler, KI-Inhalte kennzeichnen."""
from __future__ import annotations

import subprocess
from pathlib import Path

def batch(paths: list, out_dir, op: str = "bw", size: int | None = None) -> dict:
    """Wendet fx.apply_filter(op) (+ optional max. Kantenlaenge size) auf alle Bilder an, speichert je eine
    neue Datei in out_dir. Originale unveraendert. Rueckgabe {"success", "outputs": [...]}."""
    raise RuntimeError("NOT_IMPLEMENTED:batch")
