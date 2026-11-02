from __future__ import annotations
from pathlib import Path

def resize(src: Path, dst: Path, size: tuple[int, int]) -> dict:
    try:
        from PIL import Image
    except Exception:
        return {"success": False, "error": "pillow_missing", "reply": "Pillow nicht installiert."}
    im = Image.open(src)
    im = im.resize(size)
    im.save(dst)
    return {"success": True, "path": str(dst)}
