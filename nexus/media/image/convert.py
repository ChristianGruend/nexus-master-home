from __future__ import annotations
from pathlib import Path

def convert(src: Path, dst: Path) -> dict:
    try:
        from PIL import Image
    except Exception:
        return {"success": False, "error": "pillow_missing"}
    im = Image.open(src)
    im.save(dst)
    return {"success": True, "path": str(dst)}
