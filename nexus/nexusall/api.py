"""HTTP-Anbindung NEXUS All (/api/nexusall/…; die Seite liefert nexus/chat/routes/ui.py). handle() gibt True zurueck,
wenn der Pfad bedient wurde; die Antwort landet in h.response (nexus/chat/routes/bridge.Compat)."""
from __future__ import annotations

import mimetypes, subprocess

from . import core

LOCAL = ("127.0.0.1", "::1")


def _is_local(h) -> bool:
    ip = h.client_address[0]
    return ip in LOCAL or ip.startswith("100.") or ip.startswith("192.168.")


def _stream(h, rel: str, qs) -> None:
    """Video ausliefern: Range/HEAD über FileResponse; .mkv/.avi bzw. ?remux=1 per ffmpeg als fragmentiertes MP4."""
    from starlette.responses import FileResponse, StreamingResponse
    fp = core.resolve(rel)
    if not fp:
        return h._json(404, {"success": False, "error": "NOT_FOUND"})
    remux = (qs.get("remux") or ["0"])[0] == "1" or fp.suffix.lower() in (".mkv", ".avi")
    if remux and (ff := core.ffmpeg_path()):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        p = subprocess.Popen([ff, "-v", "error", "-i", str(fp), "-c", "copy", "-movflags", "frag_keyframe+empty_moov",
                              "-f", "mp4", "pipe:1"], stdout=subprocess.PIPE, creationflags=flags, env=core._env())

        def pipe():
            try:
                while chunk := p.stdout.read(256 * 1024):
                    yield chunk
            finally:
                p.kill()
        h.response = StreamingResponse(pipe(), media_type="video/mp4")
        return
    h.response = FileResponse(fp, media_type=mimetypes.guess_type(str(fp))[0] or "application/octet-stream")


def handle(h, method: str, path: str, qs, body) -> bool:
    if not path.startswith("/api/nexusall/"):
        return False
    if not _is_local(h):
        h._json(403, {"success": False, "error": "LOCAL_ONLY"}); return True
    sub = path[len("/api/nexusall/"):]
    b = body if isinstance(body, dict) else {}
    q1 = lambda k, d="": (qs.get(k) or [d])[0]
    if method == "GET":
        if sub == "health":
            h._json(200, core.health()); return True
        if sub == "library":
            h._json(200, core.library(q1("q"), q1("type"))); return True
        if sub == "queue":
            h._json(200, core.queue()); return True
        if sub == "trash":
            h._json(200, core.trash_list()); return True
        if sub.startswith("stream/"):
            _stream(h, sub[7:], qs); return True
        if sub.startswith("thumb/"):
            t = core.thumb(sub[6:])
            if not t:
                h._json(404, {"success": False, "error": "NO_THUMB"})
            else:
                h._bytes(200, t.read_bytes(), "image/jpeg")
            return True
    if method == "POST":
        if sub == "download":
            r = core.enqueue(str(b.get("url") or ""), str(b.get("quality") or "best"), str(b.get("subs") or ""),
                             loudnorm=bool(b.get("loudnorm")))
            h._json(200 if r.get("success") else 400, r); return True
        if sub == "delete":  # NA01: nur Papierkorb, nie endgueltig
            if not b.get("confirm"):
                h._json(400, {"success": False, "error": "CONFIRM_REQUIRED"}); return True
            r = core.trash_item(str(b.get("id") or "")); h._json(200 if r.get("success") else 404, r); return True
        if sub in ("trash/restore", "trash/purge"):
            if sub == "trash/purge" and b.get("confirm") != "ENDGUELTIG":
                h._json(400, {"success": False, "error": "CONFIRM_REQUIRED"}); return True
            fn = core.trash_restore if sub.endswith("restore") else core.trash_purge
            r = fn(str(b.get("trash_id") or "")); h._json(200 if r.get("success") else 404, r); return True
        if sub == "probe":
            h._json(200, core.probe(str(b.get("url") or ""))); return True
        if sub == "resume":
            h._json(200, core.save_resume(str(b.get("id") or ""), float(b.get("pos") or 0))); return True
        if sub == "open_window":
            if h.client_address[0] not in LOCAL:
                h._json(403, {"success": False, "error": "LOOPBACK_ONLY"}); return True
            h._json(200, core.open_app_window(str(b.get("url") or ""))); return True
        if sub == "voice":
            h._json(200, {"success": True, "intent": core.voice_intent(str(b.get("text") or ""))}); return True
        if sub.startswith("queue/"):
            parts = sub.split("/")
            if len(parts) == 3 and parts[2] in ("cancel", "retry", "remove"):
                fn = {"cancel": core.cancel, "retry": core.retry, "remove": core.remove_job}[parts[2]]
                h._json(200, fn(parts[1])); return True
    h._json(404, {"success": False, "error": "UNKNOWN_ENDPOINT"})
    return True
