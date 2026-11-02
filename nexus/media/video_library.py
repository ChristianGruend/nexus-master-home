"""Lokale Videos + URL-Play-Hinweise fuer In-App-Player."""
from __future__ import annotations
from pathlib import Path
from nexus.common.paths import MEDIA_DIR, DOWNLOADS_DIR, ensure_dirs
from .playlist import VIDEO_EXT, scan
from .stream import detect_embed, normalize_url


class VideoLibrary:
    def list(self) -> dict:
        ensure_dirs()
        items = []
        for root, label in ((MEDIA_DIR, "media"), (DOWNLOADS_DIR, "downloads")):
            for it in scan(root):
                if it.get("kind") == "video":
                    items.append({
                        **it,
                        "source": label,
                        "play_url": f"/{label}/{it['path']}" if label == "media" else f"/downloads/{it['path']}",
                    })
        # also scan media for only video via rglob if scan mixes — already filtered
        return {"success": True, "items": items, "count": len(items)}

    def resolve_url(self, url: str) -> dict:
        norm = normalize_url(url or "")
        if not norm:
            return {"success": False, "error": "bad_url", "reply": "Nur http/https."}
        emb = detect_embed(norm)
        low = norm.lower()
        direct = any(low.split("?")[0].endswith(ext) for ext in VIDEO_EXT | {".m3u8", ".mov"})
        return {
            "success": True,
            "url": norm,
            "mode": "embed" if emb else ("direct" if direct else "link"),
            "embed": emb,
            "reply": (emb["note"] if emb else ("Direktvideo — starte in NEXUS." if direct else "Bitte YouTube-Link oder mp4/webm-URL verwenden.")),
        }
