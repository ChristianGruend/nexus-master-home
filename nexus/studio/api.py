"""NEXUS Studio HTTP-API (/api/studio/*). Gleiche JSON-API ist fuer spaetere NEXUS OS Mobile Anbindung gedacht
(dann ueber Bridge-Token freigeben); aktuell nur Loopback."""
from __future__ import annotations

import threading
from pathlib import Path

from nexus.studio.core import Studio, StudioError, get_studio, taxonomy, _live

LOOPBACK = ("127.0.0.1", "::1")
_JOB = {"running": False, "done": 0, "total": 0, "errors": []}


def _cat_job(studio: Studio, ids: list[str]) -> None:
    _JOB.update(running=True, done=0, total=len(ids), errors=[])
    _live("NEXUS", "studio", f"Kategorisierung gestartet: {len(ids)} Bilder (llava lokal)")
    for iid in ids:
        try:
            studio.categorize(iid)
        except Exception as e:
            _JOB["errors"].append({"id": iid, "error": getattr(e, "code", type(e).__name__)})
        _JOB["done"] += 1
    _JOB["running"] = False
    _live("NEXUS", "studio", f"Kategorisierung fertig: {_JOB['done']} / Fehler {len(_JOB['errors'])}", ok=not _JOB["errors"])


def handle(method: str, path: str, qs: dict, body: dict | None, client_ip: str, studio: Studio | None = None):
    """Rueckgabe (status, payload) oder (status, {"__file__": Path, "ctype": str})."""
    studio = studio or get_studio()
    q = lambda k, d="": (qs.get(k) or [d])[0]
    if client_ip not in LOOPBACK:
        return 403, {"success": False, "error": "LOOPBACK_ONLY"}
    b = body if isinstance(body, dict) else {}
    try:
        if method == "GET":
            if path == "/api/studio/status":
                return 200, {"success": True, "stats": studio.stats(), "job": _JOB, "taxonomy": taxonomy(),
                             "ops": list(Studio.OPS), "blocked_ops": Studio.BLOCKED_OPS, "library": str(studio.base)}
            if path == "/api/studio/library":
                return 200, {"success": True, "items": studio.search(q("q"), q("category"), q("from"), q("to"), int(q("min_quality", "0") or 0))}
            if path == "/api/studio/fx":
                from nexus.studio import fx
                return 200, {"success": True, "filters": fx.FILTER_LABELS, "adjust": list(fx.ADJUST_KEYS),
                             "formats": {k: list(v) for k, v in fx.FORMATS.items()}, "templates": list(fx.TEMPLATES), "fonts": list(fx.FONTS)}
            if path.startswith("/api/studio/social/"):
                fp = studio.base / "social" / Path(path.rsplit("/", 1)[1]).name
                if not fp.exists():
                    raise StudioError("NOT_FOUND", 404)
                return 200, {"__file__": fp, "ctype": "image/png"}
            if path == "/api/studio/duplicates":
                return 200, {"success": True, **studio.duplicates()}
            if path == "/api/studio/similar":
                return 200, {"success": True, "items": studio.similar(q("id"))}
            if path.startswith("/api/studio/img/"):
                iid, _, kind = path[len("/api/studio/img/"):].partition("/")
                it = studio.get(iid)
                if it.get("category") == "Privat (gesperrt)" and kind != "thumb":
                    return 403, {"success": False, "error": "PRIVATE_LOCKED"}
                if kind == "thumb":
                    fp = studio.base / "thumbs" / f"{iid}.jpg"
                elif kind == "original":
                    fp = studio.base / "originals" / it["copy"]
                else:
                    v = next((v for v in it["versions"] if f"v{v['n']}" == kind), None)
                    if not v:
                        raise StudioError("NOT_FOUND", 404)
                    fp = studio.base / "versions" / v["file"]
                return 200, {"__file__": fp, "ctype": "image/png" if fp.suffix.lower() == ".png" else "image/jpeg"}
        if method == "POST":
            if path == "/api/studio/import":
                paths = b.get("paths") or []
                if b.get("folder"):
                    from nexus.studio.core import IMG_EXT
                    folder = Path(str(b["folder"]))
                    paths = sorted(str(p) for p in folder.iterdir() if p.suffix.lower() in IMG_EXT)[: int(b.get("limit") or 50)]
                return 200, studio.import_files(paths, limit=int(b.get("limit") or 50))
            if path == "/api/studio/categorize":
                if _JOB["running"]:
                    return 409, {"success": False, "error": "JOB_RUNNING", "job": _JOB}
                ids = b.get("ids") or [k for k, v in studio.items.items() if b.get("all") or not v.get("category")]
                threading.Thread(target=_cat_job, args=(studio, list(ids)), daemon=True, name="studio-cat").start()
                return 202, {"success": True, "started": len(ids)}
            if path in ("/api/studio/edit", "/api/studio/command") and studio.items.get(str(b.get("id")), {}).get("category") == "Privat (gesperrt)":
                return 403, {"success": False, "error": "PRIVATE_LOCKED"}
            if path == "/api/studio/edit":
                return 200, {"success": True, "version": studio.edit(str(b.get("id")), str(b.get("op")), rect=b.get("rect"), color=b.get("color"))}
            if path in ("/api/studio/filter", "/api/studio/adjust", "/api/studio/social"):  # ST02-ST04 (20261028)
                from nexus.studio import fx
                if path == "/api/studio/social":
                    if b.get("bg_id") and studio.items.get(str(b["bg_id"]), {}).get("category") == "Privat (gesperrt)":
                        return 403, {"success": False, "error": "PRIVATE_LOCKED"}
                    return 200, {"success": True, **fx.studio_social(studio, b)}
                if studio.items.get(str(b.get("id")), {}).get("category") == "Privat (gesperrt)":
                    return 403, {"success": False, "error": "PRIVATE_LOCKED"}
                if path == "/api/studio/filter":
                    return 200, {"success": True, **fx.studio_filter(studio, str(b.get("id")), str(b.get("name")), bool(b.get("preview")))}
                return 200, {"success": True, **fx.studio_adjust(studio, str(b.get("id")), b.get("params") or {}, bool(b.get("preview")))}
            if path == "/api/studio/command":
                from nexus.studio.commands import run_command
                return 200, {"success": True, **run_command(studio, str(b.get("id")), str(b.get("text") or ""), rect=b.get("rect"))}
            if path == "/api/studio/verify":
                return 200, {"success": True, **studio.verify()}
            if path == "/api/studio/organize/propose":
                return 200, {"success": True, **studio.propose_folders()}
            if path == "/api/studio/organize/apply":
                return 200, {"success": True, **studio.apply_folders(str(b.get("nonce") or ""), b.get("confirm") is True)}
        return 404, {"success": False, "error": "UNKNOWN_STUDIO_ROUTE"}
    except StudioError as e:
        return e.status, {"success": False, "error": e.code}
    except (OSError, ValueError, TypeError) as e:
        return 400, {"success": False, "error": type(e).__name__}