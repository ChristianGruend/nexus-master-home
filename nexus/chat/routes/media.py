"""Medien, Streams, Downloads, Sprache (STT/TTS/Anruf), Vision, Studio und NEXUS All."""
from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

from starlette.responses import Response
from starlette.routing import Route

from nexus.chat.engine import response_identity
from nexus.chat.routes import BOTH, ENGINE, GET, POST, body, coded, peer, qs, reply
from nexus.common.paths import SCREENS_DIR, ensure_dirs

VOICE_GET = ("/api/voice/wakeword/state", "/api/voice/commands/status")
VOICE_POST = ("/api/voice/command", "/api/voice/confirm", "/api/voice/wakeword/config",
              "/api/voice/wakeword/heartbeat", "/api/voice/live_state")


def _b64(v) -> bytes:
    v = str(v or "")
    return base64.b64decode(v.split(",", 1)[1] if "," in v else v)


def voice_status(r):
    vs = ENGINE.master.voice.status()
    try:
        vs["provider_route"] = ENGINE.master.ai.route()
    except Exception as e:
        vs["provider_route"] = {"error": type(e).__name__}
    return reply(vs)


def voice_commands(r):
    """heynexus-20261006: Sprachbefehle, Bestätigung, Weckwort; PC-Bestätigungen (pc-…) nie über den WhatsApp-Pfad."""
    from nexus.voice import commands
    if r.method == "GET":
        return coded(commands.handle_get(r.url.path, qs(r), ENGINE))
    b = body(r)
    if r.url.path == "/api/voice/confirm" and str(b.get("id") or "").startswith("pc-"):
        try:
            from nexus.pc_remote import voice_confirm
            return coded(voice_confirm(b, peer(r), r.headers, ENGINE))
        except Exception:
            return reply({"success": False, "error": "PC_COMMAND_SERVICE_UNAVAILABLE"}, 503)
    return coded(commands.handle_post(r.url.path, b, ENGINE))


def voice_chat(r):
    b = body(r)
    res = ENGINE.chat(b.get("message") or b.get("text") or "", b.get("session_id"), b.get("project_id"), model=b.get("model"))
    res["tts_url"] = "/api/voice/tts"
    return reply(res)


def voice_call(r):  # NEXUSPhone KI-Anruf: ein Gesprächszug (Sprache -> NEXUS oder Hermes -> Sprache)
    from nexus.voice import call
    b, step = body(r), r.path_params["step"]
    fn = {"start": lambda: call.warm(ENGINE, b), "turn": lambda: call.turn(ENGINE, b), "next": lambda: call.next_segment(b),
          "action": lambda: call.action(b), "end": lambda: call.end(b, ENGINE)}.get(step)
    return coded(fn()) if fn else reply({"success": False, "error": "not_found", "path": r.url.path}, 404)


def voice_turn(r):
    """STT -> Chat -> optional TTS (Amala). JSON rein/raus, kein Fake-PASS."""
    from nexus.voice import stt
    b = body(r)
    raw = b""
    if b.get("audio_b64") or b.get("audio"):
        try:
            raw = _b64(b.get("audio_b64") or b.get("audio"))
        except Exception:
            return reply({"success": False, "error": "bad_audio_b64"}, 400)
    text_in, stt_info = (b.get("message") or b.get("text") or "").strip(), None
    if raw:
        stt_info = stt.transcribe_bytes(raw, filename=b.get("filename") or "audio.webm", language=b.get("language") or "de",
                                        model_size=b.get("model") or "tiny")
        if not stt_info.get("success"):
            return reply({"success": False, "error": stt_info.get("error") or "stt_failed",
                          "reply": stt_info.get("reply") or "STT fehlgeschlagen.", "stt": stt_info})
        text_in = (stt_info.get("text") or "").strip()
    if not text_in:
        return reply({"success": False, "error": "empty_transcript", "reply": "Kein Text erkannt — bitte erneut sprechen.", "stt": stt_info})
    from nexus.voice.commands import voice_priv_block  # nexus_voice_priv_guard
    if blocked := voice_priv_block(text_in):
        return reply({**blocked, "text": text_in, "stt": stt_info})
    chat = ENGINE.chat(text_in, b.get("session_id"), b.get("project_id"), model=b.get("model"))
    out = {"success": bool(chat.get("success", True)), "text": text_in, "reply": chat.get("reply") or "",
           **{k: chat.get(k) for k in ("intent", "model", "session_id", "error", "data")},
           **response_identity(chat), "stt": stt_info, "tts_url": "/api/voice/tts"}
    if b.get("speak", True) and out["reply"]:
        tts = ENGINE.master.voice.tts(out["reply"][:1800])
        if tts.get("success") and tts.get("path"):
            mp3 = Path(tts["path"]).read_bytes()
            # Standard-Syntheseprofil; die Partner-Identität der Antwort bedeutet keine Partnerstimme.
            out.update(tts_b64=base64.b64encode(mp3).decode("ascii"), tts_bytes=len(mp3), tts_voice=tts.get("voice"),
                       tts_profile="configured_default",
                       **{"tts_" + k: tts[k] for k in ("mode", "rate", "pitch", "volume") if tts.get(k) is not None})
        else:
            out["tts_error"] = tts.get("error") or "tts_failed"
    return reply(out)


def voice_tts(r):
    b = body(r)
    try:  # heynexus-20261006: Weckwort-Dienst pausiert, solange NEXUS spricht
        from nexus.voice import commands
        commands.note_tts(b.get("text", "") or "", b.get("source"))
    except Exception:
        pass
    opts = {k: str(b[k]) for k in ("voice", "rate", "pitch", "volume") if b.get(k) is not None and (k != "voice" or b[k])}
    if isinstance(b.get("voices"), list):
        opts["voices"] = [str(v) for v in b["voices"][:3] if v]
    res = ENGINE.master.voice.tts(b.get("text", ""), **opts)
    if res.get("success") and res.get("path"):
        return Response(Path(res["path"]).read_bytes(), media_type="audio/mpeg")
    return reply(res)


def voice_speaker(r):  # 008-P06: lokale Sprechererkennung (offline)
    from nexus.voice import speaker_id
    action, b = r.path_params["action"], body(r)
    if action == "status":
        return reply(speaker_id.status())
    try:
        if action == "enroll":
            return reply(speaker_id.enroll(b.get("name"), [_b64(x) for x in (b.get("samples_b64") or [b.get("audio_b64")]) if x]))
        if action == "identify":
            return reply(speaker_id.identify(_b64(b.get("audio_b64"))))
    except Exception as exc:
        return reply({"success": False, "error": str(exc)}, 400)
    return reply({"success": False, "error": "not_found", "path": r.url.path}, 404)


def voice_stt(r):
    from nexus.voice import stt
    b = body(r)
    try:
        raw = _b64(b.get("audio_b64") or b.get("audio") or "")
    except Exception:
        return reply({"success": False, "error": "bad_audio_b64"}, 400)
    if not raw:
        return reply({"success": False, "error": "empty_audio", "hint": "POST JSON {audio_b64, filename?, language?}"}, 400)
    return reply(stt.transcribe_bytes(raw, filename=b.get("filename") or "audio.wav", language=b.get("language") or "de",
                                      model_size=b.get("model") or "tiny"))


def vision(r):
    b = body(r)
    try:
        img = _b64(b.get("image") or b.get("image_b64") or "")
    except Exception:
        return reply({"success": False, "error": "bad_image"}, 400)
    if not img:
        return reply({"success": False, "error": "empty_image"}, 400)
    if r.url.path == "/api/screen/analyze" or b.get("save"):
        ensure_dirs()
        SCREENS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            (SCREENS_DIR / f"frame_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg").write_bytes(img)
        except Exception:
            pass
        ENGINE.master.last_screen_frame = img
    prompt = b.get("prompt") or "Beschreibe das Bild auf Deutsch."
    try:  # Phase 10: multimodale Bild-Fassade (resolve_vision_model + keep_alive=0)
        from nexus.multimodal.image import analyze
        return reply(analyze(ENGINE.master, img, prompt))
    except Exception:
        return reply(ENGINE.master.llm.vision(prompt, img))


def studio(r):  # NEXUS Studio (20261028)
    from nexus.studio.api import handle
    code, payload = handle(r.method, r.url.path, qs(r) if r.method == "GET" else {}, body(r) if r.method == "POST" else None, peer(r))
    if "__file__" in payload:
        if not payload["__file__"].is_file():
            return reply({"success": False, "error": "NOT_FOUND"}, 404)
        return Response(payload["__file__"].read_bytes(), media_type=payload["ctype"])
    return reply(payload, code)


def nexusall(r):  # NEXUS All (20261028)
    from nexus.chat.routes.bridge import Compat
    from nexus.nexusall.api import handle
    h = Compat(r)
    handle(h, r.method, r.url.path, qs(r) if r.method == "GET" else {}, body(r) if r.method == "POST" else None)
    return h.response


def _videos(r):
    from nexus.media.video_library import VideoLibrary
    return reply(VideoLibrary().list() if r.method == "GET" else VideoLibrary().resolve_url(body(r).get("url") or ""))


def _gallery(r):
    from nexus.media.gallery import list_gallery
    return reply(list_gallery())


def _media_library(r):
    ENGINE.master.media.refresh()
    return reply({"success": True, "state": ENGINE.master.media.state()})


def _stream_id(r):
    return body(r).get("id") or body(r).get("stream_id")


routes = [
    Route("/api/media", lambda r: reply({"success": True, "state": ENGINE.master.media.state()}), methods=GET),
    Route("/api/media/library", _media_library, methods=GET),
    Route("/api/media/play_at", lambda r: reply(ENGINE.master.media.play_at(int(body(r).get("index") or 0))), methods=POST),
    *[Route(f"/api/media/{a}", lambda r, a=a: reply(getattr(ENGINE.master.media, a)()), methods=POST)
      for a in ("play", "resume", "pause", "next", "prev", "stop")],
    Route("/api/streams", lambda r: reply(ENGINE.master.streams.list()), methods=GET),
    Route("/api/streams", lambda r: reply(ENGINE.master.streams.add(body(r).get("url", ""), body(r).get("title"))), methods=POST),
    Route("/api/streams/remove", lambda r: reply(ENGINE.master.streams.remove(_stream_id(r))), methods=POST),
    Route("/api/streams/active", lambda r: reply(ENGINE.master.streams.set_active(_stream_id(r))), methods=POST),
    Route("/api/streams/comment", lambda r: reply(ENGINE.master.streams.set_comment(_stream_id(r) or ENGINE.master.streams.active_id,
                                                                         body(r).get("comment") or body(r).get("text") or "")), methods=POST),
    Route("/api/downloads", lambda r: reply(ENGINE.master.downloads.list()), methods=GET),
    Route("/api/downloads", lambda r: reply(ENGINE.master.downloads.enqueue(body(r).get("url", ""))), methods=POST),
    Route("/api/videos", _videos, methods=GET),
    Route("/api/videos/resolve", _videos, methods=POST),
    Route("/api/gallery", _gallery, methods=GET),
    Route("/api/voice/status", voice_status, methods=GET),
    *[Route(p, voice_commands, methods=GET) for p in VOICE_GET],
    *[Route(p, voice_commands, methods=POST) for p in VOICE_POST],
    Route("/api/voice/chat", voice_chat, methods=POST),
    Route("/api/voice/call/{step}", voice_call, methods=POST),
    Route("/api/voice/turn", voice_turn, methods=POST),
    Route("/api/voice/speak_pc", lambda r: reply(ENGINE.master.voice.speak_pc(body(r).get("text", "") or "", wait=body(r).get("wait") is None or bool(body(r).get("wait")))), methods=POST),
    Route("/api/voice/tts", voice_tts, methods=POST),
    Route("/api/voice/speaker/{action}", voice_speaker, methods=POST),
    Route("/api/voice/stt", voice_stt, methods=POST),
    Route("/api/vision", vision, methods=POST),
    Route("/api/screen/analyze", vision, methods=POST),
    Route("/api/studio/{rest:path}", studio, methods=BOTH),
    Route("/api/nexusall/{rest:path}", nexusall, methods=BOTH),
]
