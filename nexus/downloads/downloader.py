from __future__ import annotations
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, unquote
from nexus.common.config import Config

def download_url(url: str, dest_dir: Path) -> dict:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = unquote(Path(urlparse(url).path).name) or "download.bin"
    # sanitize
    name = "".join(c for c in name if c.isalnum() or c in "._- ")[:180] or "download.bin"
    dest = dest_dir / name
    # unique
    i = 1
    while dest.exists():
        dest = dest_dir / f"{dest.stem}_{i}{dest.suffix}"
        i += 1
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-AI/1.0"})
        with urllib.request.urlopen(req, timeout=Config.DOWNLOAD_TIMEOUT) as resp:
            data = resp.read()
        dest.write_bytes(data)
        return {"success": True, "path": str(dest), "bytes": len(data), "name": dest.name}
    except Exception as e:
        return {"success": False, "error": str(e)}
