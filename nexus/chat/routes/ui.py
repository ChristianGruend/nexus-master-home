"""UI-Seiten, Static, Login, geprüfte Vorschauen und Dateiauslieferung (/media, /downloads, /screens)."""
from __future__ import annotations

import hmac
import json
import mimetypes
import re
from html import escape

from starlette.responses import FileResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from nexus.chat.routes import GET, ROOT, arg, is_within, local, page, reply
from nexus.common import api_auth
from nexus.common.paths import DOWNLOADS_DIR, MEDIA_DIR, SCREENS_DIR, STATIC_DIR

PAGES = {"m.html": ("/m", "/m/", "/m.html"), "m_classic.html": ("/m-classic", "/m-classic/"),
         "m2.html": ("/m2", "/m2/", "/m2.html"), "mail.html": ("/mail", "/mail.html"),
         "studio.html": ("/studio", "/studio.html"), "swarm.html": ("/swarm", "/swarm.html"),
         "live_monitor.html": ("/live", "/live_monitor.html"),
         "nexusall.html": ("/nexusall", "/nexusall/", "/nexusall.html")}
PREVIEW_DIR = ROOT / "workspace" / "projects" / "hermes" / "preview"
_NAME = re.compile(r"[A-Za-z0-9_-]{1,80}")


def home(r):
    """Hauptseite bzw. Freigaben; lokale Browser-Navigation bekommt das HttpOnly-UI-Cookie (Stufe 2.1a)."""
    resp = page("approvals.html" if r.url.path.startswith("/approvals") else "index.html")
    h = r.headers
    if local(r) and h.get("Sec-Fetch-Mode") == "navigate" and h.get("Sec-Fetch-Dest") == "document":
        try:
            from nexus.terminal import guard
            resp.headers.append("Set-Cookie", guard.ui_cookie())
        except Exception:
            pass
    return resp


def service_worker(r):
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript; charset=utf-8",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


def manifest(r):
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json; charset=utf-8")


def login(r):
    """Browser-Login für Fernzugriff: /login?token=<API-Token>[&next=/m] setzt das Session-Cookie."""
    token, nxt = arg(r, "token").strip(), arg(r, "next", "/")
    if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
        nxt = "/"
    if token and hmac.compare_digest(token.encode(), api_auth.api_token().encode()):
        secure = (r.headers.get("X-Forwarded-Proto") or "").lower() == "https"
        return RedirectResponse(nxt, 303, {"Set-Cookie": api_auth.session_cookie(secure=secure), "Cache-Control": "no-store"})
    html = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>NEXUS Login</title><body style='font-family:sans-serif;max-width:28em;margin:3em auto'>"
            "<h1>NEXUS Login</h1>" + ("<p style='color:#c00'>Token ungültig.</p>" if token else "")
            + "<form method=get action=/login><input type=hidden name=next value='" + escape(nxt, quote=True) + "'>"
            "<input name=token type=password placeholder='API-Token' style='width:100%' autofocus> "
            "<button>Anmelden</button></form>"
            "<p>Token anzeigen: <code>python -m nexus.common.api_auth</code></p></body>")
    return Response(html, 401 if token else 200, {"Cache-Control": "no-store"}, "text/html; charset=utf-8")


def _preview_current(meta_path) -> bool:
    """Nur Vorschauen, deren Prüfung die aktuelle Kriterienversion erfüllt hat."""
    from nexus.bridge.web_check import CRITERIA_VERSION
    try:
        return json.loads(meta_path.read_text(encoding="utf-8")).get("criteria_version") == CRITERIA_VERSION
    except (OSError, ValueError):
        return False


def previews(r):
    """Geprüfte Vorschauen mit Startlink und Prüfbeleg (für Chamber und Oberfläche)."""
    out = []
    for meta in sorted(PREVIEW_DIR.glob("*/preview.json")) if PREVIEW_DIR.is_dir() else []:
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not _preview_current(meta):
            continue
        entry = next((n for n in info.get("sha256", {}) if n.endswith("index.html")), None)
        out.append({"project": info.get("project"), "url": "/preview/" + str(info.get("project")) + "/" + str(entry or "index.html"),
                    "check_task_id": info.get("check_task_id"), "publish_task_id": info.get("publish_task_id"),
                    "published_at": info.get("published_at"), "stage": info.get("stage")})
    return reply({"success": True, "previews": out})


def preview(r):
    """Nur geprüfte Vorschauen (workspace/projects/hermes/preview/<projekt>/), streng isoliert.

    CSP "sandbox allow-scripts": eigene, undurchsichtige Herkunft ohne Zugriff auf NEXUS-
    APIs, Cookies oder Speicher; connect-src 'none' sperrt jede Netzwerkanfrage.
    """
    parts = r.path_params["rel"].split("/")
    types = {"html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8",
             "js": "application/javascript; charset=utf-8", "json": "application/json; charset=utf-8"}
    if (len(parts) != 2 or not _NAME.fullmatch(parts[0])
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", parts[1]) or parts[1].startswith(".")
            or parts[1].rsplit(".", 1)[-1].lower() not in types or parts[1] == "preview.json"):
        return reply({"success": False, "error": "preview_not_found"}, 404)
    base = (PREVIEW_DIR / parts[0]).resolve()
    target = (base / parts[1]).resolve()
    if base not in target.parents or not target.is_file() or not (base / "preview.json").is_file():
        return reply({"success": False, "error": "preview_not_found"}, 404)
    if not _preview_current(base / "preview.json"):
        # Nach älteren (schwächeren) Kriterien geprüft: nicht mehr als geprüfte Vorschau ausliefern.
        return reply({"success": False, "error": "preview_outdated_recheck_required"}, 409)
    return Response(target.read_bytes(), 200, {
        "Content-Security-Policy": "sandbox allow-scripts allow-pointer-lock; default-src 'none'; script-src 'self' 'unsafe-inline'; "
                                   "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
                                   "media-src 'self' data: blob:; connect-src 'none'; base-uri 'none'; form-action 'none'",
        "X-Content-Type-Options": "nosniff", "Cross-Origin-Opener-Policy": "same-origin", "Cache-Control": "no-store",
    }, types[parts[1].rsplit(".", 1)[-1].lower()])


def evidence(r):
    """Nur PNG-Screenshots der Browserprüfungen (Beleg), keine anderen Dateien."""
    parts = r.path_params["rel"].split("/")
    target = ROOT / "workspace" / "projects" / "hermes" / "preview_evidence" / "/".join(parts)
    if (len(parts) != 2 or not _NAME.fullmatch(parts[0])
            or not re.fullmatch(r"(?:check|preview)-\d{8}-\d{6}\.png", parts[1]) or not target.is_file()):
        return reply({"success": False, "error": "evidence_not_found"}, 404)
    return FileResponse(target, media_type="image/png")


def serve_file(root, rel: str):
    """Datei unterhalb von root; Traversal -> 404, geschützte Pfade (Stufe 2.1b) -> 403."""
    root = root.resolve()
    fp = (root / rel).resolve()
    if not is_within(root, fp) or not fp.is_file():
        return reply({"success": False, "error": "not found"}, 404)
    from nexus.safety.protected import is_protected
    if is_protected(fp):
        return reply({"success": False, "error": "PROTECTED_PATH"}, 403)
    return FileResponse(fp, media_type=mimetypes.guess_type(fp.name)[0] or "application/octet-stream")


routes = [
    *[Route(p, home, methods=GET) for p in ("/", "/index.html", "/approvals", "/approvals.html")],
    *[Route(p, lambda r, f=f: page(f), methods=GET) for f, paths in PAGES.items() for p in paths],
    Route("/sw.js", service_worker, methods=GET),
    Route("/static/sw.js", service_worker, methods=GET),
    Route("/manifest.webmanifest", manifest, methods=GET),
    Route("/static/manifest.webmanifest", manifest, methods=GET),
    Mount("/static", StaticFiles(directory=STATIC_DIR)),
    Route("/login", login, methods=GET),
    Route("/api/previews", previews, methods=GET),
    Route("/preview/{rel:path}", preview, methods=GET),
    Route("/preview-evidence/{rel:path}", evidence, methods=GET),
    *[Route(f"/{name}/{{rel:path}}", lambda r, d=d: serve_file(d, r.path_params["rel"]), methods=GET)
      for name, d in (("media", MEDIA_DIR), ("downloads", DOWNLOADS_DIR), ("screens", SCREENS_DIR))],
]
