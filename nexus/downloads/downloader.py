from __future__ import annotations
import os
from pathlib import Path
from urllib.parse import urlparse, unquote
from nexus.common.config import Config
from nexus.common.net_guard import BlockedURL, fetch_to_file

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
    max_bytes = int(os.environ.get("NEXUS_DOWNLOAD_MAX_MB") or 2048) * 1024 * 1024
    try:
        n = fetch_to_file(url, dest, timeout=Config.DOWNLOAD_TIMEOUT, max_bytes=max_bytes)
        return {"success": True, "path": str(dest), "bytes": n, "name": dest.name}
    except BlockedURL as e:
        return {"success": False, "error": "BLOCKED:" + str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}
