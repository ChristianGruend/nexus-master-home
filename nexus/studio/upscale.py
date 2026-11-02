"""NEXUS Studio Hochskalieren (Real-ESRGAN ncnn-vulkan, AMD) (Seed 2026-10-29, ST12). Nur lokal, nur G:, 0 EUR.
Leitbild: Original bleibt unveraendert (neue Version), ehrliche Fehler, KI-Inhalte kennzeichnen."""
from __future__ import annotations

import subprocess
from pathlib import Path

EXE = Path(r"G:\meine-ki\tools\realesrgan\realesrgan-ncnn-vulkan.exe")  # getestet RX 6600 XT: 220px x4 in 6,5 s


def upscale(src, out, scale: int = 4) -> dict:
    """scale 2/3/4. Aufruf: EXE -i src -o out -n realesr-animevideov3 -s scale (bzw. realesrgan-x4plus bei 4).
    Rueckgabe {"success", "out", "scale", "sec"}; Fehler ehrlich {"success": False, "error"}."""
    raise RuntimeError("NOT_IMPLEMENTED:upscale")
