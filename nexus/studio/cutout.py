"""NEXUS Studio Freistellen (U2NetP ONNX, CPU) (Seed 2026-10-29, ST11). Nur lokal, nur G:, 0 EUR.
Leitbild: Original bleibt unveraendert (neue Version), ehrliche Fehler, KI-Inhalte kennzeichnen."""
from __future__ import annotations

import subprocess
from pathlib import Path

MODEL = Path(r"G:\meine-ki\models\u2net\u2netp.onnx")  # 4,6 MB, Apache-2.0, aus rembg-Releases


def remove_bg(src, out) -> dict:
    """Hintergrund entfernen -> PNG RGBA (Alpha aus U2NetP-Maske, onnxruntime CPU, 320x320 Eingang,
    Normierung mean=(0.485,0.456,0.406) std=(0.229,0.224,0.225)). Rueckgabe {"success", "out", "model"}."""
    raise RuntimeError("NOT_IMPLEMENTED:remove_bg")
