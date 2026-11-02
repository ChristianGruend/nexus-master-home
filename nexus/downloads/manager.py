from __future__ import annotations
from datetime import datetime
from nexus.common.paths import DOWNLOADS_DIR, ensure_dirs
from .validator import validate_url
from .downloader import download_url
from .job import DownloadJob
from .queue import DownloadQueue

class DownloadManager:
    def __init__(self) -> None:
        ensure_dirs()
        self.queue = DownloadQueue()

    def list(self) -> dict:
        files = []
        for p in sorted(DOWNLOADS_DIR.iterdir()) if DOWNLOADS_DIR.exists() else []:
            if p.is_file():
                files.append({"name": p.name, "size": p.stat().st_size})
        return {"success": True, "jobs": self.queue.list(), "files": files}

    def enqueue(self, url: str) -> dict:
        ok, val = validate_url(url)
        if not ok:
            return {"success": False, "error": val, "reply": val}
        jid = f"DL-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        job = DownloadJob(id=jid, url=val, status="running")
        self.queue.add(job)
        result = download_url(val, DOWNLOADS_DIR)
        if result.get("success"):
            job.status = "done"
            job.path = result.get("path")
            job.bytes = result.get("bytes", 0)
            reply = f"Download fertig: {result.get('name')} ({job.bytes} Bytes)"
            return {"success": True, "job": job.to_dict(), "reply": reply}
        job.status = "error"
        job.error = result.get("error")
        return {"success": False, "job": job.to_dict(), "reply": f"Download fehlgeschlagen: {job.error}", "error": job.error}
