"""Galerie: Bilder aus workspace/media, screens, downloads + static refs."""
from __future__ import annotations
from pathlib import Path
from nexus.common.paths import MEDIA_DIR, DOWNLOADS_DIR, SCREENS_DIR, STATIC_DIR, ensure_dirs

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}


def _rel_items(root: Path, url_prefix: str, source: str, limit: int = 200) -> list:
    items = []
    if not root.exists():
        return items
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in IMAGE_EXT:
            continue
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        items.append({
            "name": p.name,
            "path": rel,
            "source": source,
            "url": f"{url_prefix.rstrip('/')}/{rel}",
            "size": p.stat().st_size,
            "kind": "image",
        })
        if len(items) >= limit:
            break
    return items


def list_gallery(limit: int = 120) -> dict:
    ensure_dirs()
    items: list = []
    items.extend(_rel_items(MEDIA_DIR, "/media", "media", limit=limit))
    items.extend(_rel_items(SCREENS_DIR, "/screens", "screens", limit=limit))
    items.extend(_rel_items(DOWNLOADS_DIR, "/downloads", "downloads", limit=limit))
    # Static refs as fallback / demo gallery
    refs = STATIC_DIR / "assets" / "refs"
    items.extend(_rel_items(refs, "/static/assets/refs", "static_refs", limit=40))
    # Dedupe by url
    seen = set()
    uniq = []
    for it in items:
        u = it["url"]
        if u in seen:
            continue
        seen.add(u)
        uniq.append(it)
    return {
        "success": True,
        "items": uniq[:limit],
        "count": len(uniq[:limit]),
        "roots": {
            "media": str(MEDIA_DIR),
            "screens": str(SCREENS_DIR),
            "downloads": str(DOWNLOADS_DIR),
        },
    }
