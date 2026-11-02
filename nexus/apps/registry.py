"""App-Registry: Dienste + lokale Windows-Apps, die NEXUS oeffnen kann."""
from __future__ import annotations
import json
from pathlib import Path
from nexus.common.paths import WORKSPACE, ensure_dirs
from nexus.common.response import attach_ok

STORE = WORKSPACE / "apps" / "connections.json"

# open_mode: embed | link | native | internal
# launch: Key fuer OSActions.launch_app (None = nur URL/Info)
CATALOG = [
    {"id": "youtube", "name": "YouTube", "category": "Medien", "open_mode": "embed",
     "url": "https://www.youtube.com", "embed_url": "https://www.youtube.com/embed?listType=playlist",
     "launch": "youtube", "wire": "sofort",
     "desc": "Videos in NEXUS oder Browser."},
    {"id": "maps", "name": "Google Maps", "category": "Navigation", "open_mode": "embed",
     "url": "https://maps.google.com", "embed_url": "https://maps.google.com/maps?q=Deutschland&output=embed",
     "launch": "maps", "wire": "sofort",
     "desc": "Karten und Orte."},
    {"id": "gmail", "name": "Gmail", "category": "Google", "open_mode": "link",
     "url": "https://mail.google.com", "launch": "gmail", "wire": "login",
     "desc": "E-Mail — Browser-Login / Connector."},
    {"id": "calendar", "name": "Google Kalender", "category": "Google", "open_mode": "link",
     "url": "https://calendar.google.com", "launch": "kalender", "wire": "login",
     "desc": "Termine — Browser / Connector."},
    {"id": "drive", "name": "Google Drive", "category": "Google", "open_mode": "link",
     "url": "https://drive.google.com", "launch": "drive", "wire": "login",
     "desc": "Cloud-Dateien."},
    {"id": "docs", "name": "Google Docs", "category": "Dokumente", "open_mode": "link",
     "url": "https://docs.google.com", "launch": None, "wire": "login",
     "desc": "Dokumente bearbeiten."},
    {"id": "chrome", "name": "Google Chrome", "category": "Browser", "open_mode": "native",
     "url": "", "launch": "chrome", "wire": "sofort",
     "desc": "Chrome starten."},
    {"id": "edge", "name": "Microsoft Edge", "category": "Browser", "open_mode": "native",
     "url": "", "launch": "edge", "wire": "sofort",
     "desc": "Edge starten."},
    {"id": "brave", "name": "Brave", "category": "Browser", "open_mode": "native",
     "url": "", "launch": "brave", "wire": "sofort",
     "desc": "Brave starten (falls installiert)."},
    {"id": "firefox", "name": "Firefox", "category": "Browser", "open_mode": "native",
     "url": "", "launch": "firefox", "wire": "sofort",
     "desc": "Firefox starten (falls installiert)."},
    {"id": "explorer", "name": "Explorer", "category": "Lokal", "open_mode": "native",
     "url": "", "launch": "explorer", "wire": "sofort",
     "desc": "Windows-Explorer / Dateien."},
    {"id": "files", "name": "NEXUS Dateien", "category": "Lokal", "open_mode": "internal",
     "url": "/api/files", "launch": None, "wire": "sofort",
     "desc": "Lokale Dateien unter workspace/files."},
    {"id": "notepad", "name": "Notepad", "category": "Lokal", "open_mode": "native",
     "url": "", "launch": "notepad", "wire": "sofort",
     "desc": "Editor."},
    {"id": "rechner", "name": "Rechner", "category": "Lokal", "open_mode": "native",
     "url": "", "launch": "rechner", "wire": "sofort",
     "desc": "Taschenrechner."},
    {"id": "fotos", "name": "Fotos", "category": "Medien", "open_mode": "native",
     "url": "", "launch": "fotos", "wire": "sofort",
     "desc": "Windows Fotos."},
    {"id": "spotify", "name": "Spotify", "category": "Medien", "open_mode": "native",
     "url": "spotify:", "launch": "spotify", "wire": "sofort",
     "desc": "Spotify-App / URI."},
    {"id": "vlc", "name": "VLC", "category": "Medien", "open_mode": "native",
     "url": "", "launch": "vlc", "wire": "sofort",
     "desc": "VLC Media Player (falls installiert)."},
    {"id": "wmplayer", "name": "Windows Media Player", "category": "Medien", "open_mode": "native",
     "url": "", "launch": "wmplayer", "wire": "sofort",
     "desc": "Klassischer Media Player."},
    {"id": "musik", "name": "Groove / Windows Musik", "category": "Medien", "open_mode": "native",
     "url": "mswindowsmusic:", "launch": "musik", "wire": "sofort",
     "desc": "Windows Musik-App."},
    {"id": "whatsapp", "name": "WhatsApp", "category": "Social", "open_mode": "native",
     "url": "https://web.whatsapp.com", "launch": "whatsapp", "wire": "sofort",
     "desc": "Desktop-App oder Web."},
    {"id": "discord", "name": "Discord", "category": "Social", "open_mode": "native",
     "url": "https://discord.com/app", "launch": "discord", "wire": "sofort",
     "desc": "Discord Desktop / Web."},
    {"id": "telegram", "name": "Telegram", "category": "Social", "open_mode": "link",
     "url": "https://web.telegram.org", "launch": "telegram", "wire": "login",
     "desc": "Web / Bot-Bridge optional."},
    {"id": "facebook", "name": "Facebook", "category": "Social", "open_mode": "link",
     "url": "https://www.facebook.com", "launch": "facebook", "wire": "login",
     "desc": "Social — Browser."},
    {"id": "tiktok", "name": "TikTok", "category": "Social", "open_mode": "link",
     "url": "https://www.tiktok.com", "launch": "tiktok", "wire": "login",
     "desc": "TikTok im Browser."},
    {"id": "steam", "name": "Steam", "category": "Games", "open_mode": "native",
     "url": "steam:", "launch": "steam", "wire": "sofort",
     "desc": "Steam Client."},
    {"id": "vscode", "name": "VS Code", "category": "Dev", "open_mode": "native",
     "url": "", "launch": "code", "wire": "sofort",
     "desc": "Visual Studio Code."},
    {"id": "cursor", "name": "Cursor", "category": "Dev", "open_mode": "native",
     "url": "", "launch": "cursor", "wire": "sofort",
     "desc": "Cursor IDE."},
    {"id": "nexus_notes", "name": "NEXUS Projektnotizen", "category": "Lokal", "open_mode": "internal",
     "url": "http://127.0.0.1:8315/", "launch": None, "wire": "sofort",
     "desc": "Lokale Projekte und Notizen mit SQLite, Suche und Projektfilter."},
    {"id": "nexus_crew", "name": "NEXUS Crew", "category": "Lokal", "open_mode": "internal",
     "url": "/api/crew", "launch": None, "wire": "sofort",
     "desc": "Spezial-Agenten: @Coder @Scout @Fixer @Media @Ops @Lens @Rap."},
    {"id": "nexus_music", "name": "NEXUS Musik-Player", "category": "Lokal", "open_mode": "internal",
     "url": "/api/media", "launch": None, "wire": "sofort",
     "desc": "Eingebauter Musik-Player mit Playlist."},
    {"id": "nexus_video", "name": "NEXUS Video-Player", "category": "Lokal", "open_mode": "internal",
     "url": "/api/videos", "launch": None, "wire": "sofort",
     "desc": "Eingebauter Video-Player."},
    {"id": "media", "name": "NEXUS Medien", "category": "Lokal", "open_mode": "internal",
     "url": "/api/media", "launch": None, "wire": "sofort",
     "desc": "Lokale Musik & Videos in NEXUS."},
    {"id": "downloads", "name": "Downloads", "category": "Lokal", "open_mode": "internal",
     "url": "/api/downloads", "launch": None, "wire": "sofort",
     "desc": "Download-Manager."},
    {"id": "code_sandbox", "name": "Code-Sandbox", "category": "Dev", "open_mode": "internal",
     "url": "/api/code/run", "launch": None, "wire": "sofort",
     "desc": "Python lokal ausfuehren."},
    {"id": "ollama", "name": "Ollama", "category": "KI", "open_mode": "internal",
     "url": "http://127.0.0.1:11434", "launch": None, "wire": "sofort",
     "desc": "Lokale Modelle (kein Pull ohne Auftrag)."},
]


class AppRegistry:
    def __init__(self) -> None:
        ensure_dirs()
        (WORKSPACE / "apps").mkdir(parents=True, exist_ok=True)
        self._conn = self._load()

    def _load(self) -> dict:
        if STORE.exists():
            try:
                return json.loads(STORE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save(self) -> None:
        STORE.write_text(json.dumps(self._conn, indent=2, ensure_ascii=False), encoding="utf-8")

    def list(self) -> dict:
        apps = []
        for a in CATALOG:
            st = self._conn.get(a["id"], {})
            connected = bool(st.get("connected"))
            status = "connected" if connected else ("stub" if a["open_mode"] == "stub" else "available")
            apps.append({
                **a,
                "status": status,
                "connected": connected,
                "connected_at": st.get("at"),
            })
        return attach_ok({"success": True, "apps": apps})

    def connect(self, app_id: str) -> dict:
        app = self.resolve_app(app_id)
        if not app:
            return attach_ok({"success": False, "error": "not_found", "reply": "App unbekannt."})
        import time
        rid = app["id"]
        self._conn[rid] = {"connected": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self._save()
        return attach_ok({
            "success": True,
            "app": {**app, "connected": True, "status": "connected"},
            "reply": f"{app['name']} verbunden (in NEXUS markiert).",
        })

    def disconnect(self, app_id: str) -> dict:
        if app_id in self._conn:
            del self._conn[app_id]
            self._save()
        return attach_ok({"success": True, "reply": "Verbindung entfernt."})

    def resolve_app(self, key: str):
        """Finde Katalog-Eintrag per id, launch-Key oder Anzeigename (case-insensitive)."""
        raw = (key or "").strip()
        if not raw:
            return None
        kl = raw.lower()
        for a in CATALOG:
            if a["id"] == raw or a["id"].lower() == kl:
                return a
        for a in CATALOG:
            launch = (a.get("launch") or "")
            if launch and launch.lower() == kl:
                return a
        for a in CATALOG:
            name = (a.get("name") or "")
            if name.lower() == kl:
                return a
        # Alias: calc -> rechner
        aliases = {"calc": "rechner", "calculator": "rechner", "notizblock": "notepad"}
        alt = aliases.get(kl)
        if alt:
            return self.resolve_app(alt)
        return None

    def open_info(self, app_id: str) -> dict:
        app = self.resolve_app(app_id)
        if not app:
            return attach_ok({"success": False, "error": "not_found", "reply": "App unbekannt."})
        return attach_ok({"success": True, "app": app})

    def open(self, app_id: str) -> dict:
        """Wirklich oeffnen: native Start-Process / Browser-URL / Info."""
        app = self.resolve_app(app_id)
        if not app:
            return attach_ok({"success": False, "error": "not_found", "reply": "App unbekannt."})
        from nexus.computer.os_actions import get_os_actions
        osact = get_os_actions()
        opened = None
        resolved_id = app["id"]
        if app.get("launch"):
            opened = osact.launch_app(app["launch"])
        elif app.get("url") and str(app["url"]).startswith(("http://", "https://", "spotify:", "steam:", "ms-")):
            opened = osact.open_url(app["url"])
        elif app.get("open_mode") == "internal":
            return attach_ok({
                "success": True,
                "app": app,
                "reply": f"{app['name']}: intern unter {app.get('url')} — im NEXUS-UI oeffnen.",
                "ui_hint": app.get("url"),
            })
        ok = bool(opened and opened.get("success"))
        # Auto-connect bei Erfolg
        if ok:
            self.connect(resolved_id)
        return attach_ok({
            "success": ok if opened else False,
            "app": app,
            "opened": opened,
            "resolved_id": resolved_id,
            "reply": (opened or {}).get("reply") or (f"{app['name']} geoeffnet." if ok else f"Konnte {app['name']} nicht oeffnen."),
        })
