"""Multimodal modality registry — honest DONE/PARTIAL/UNKNOWN/STUB matrix."""
from __future__ import annotations

from typing import Any


STATUS_DONE = "DONE"
STATUS_PARTIAL = "PARTIAL"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_STUB = "STUB"


def _text_status(master: Any = None) -> dict[str, Any]:
    """Chat/text via local Ollama (qwen)."""
    reachable = False
    model = None
    err = None
    try:
        llm = None
        if master is not None and hasattr(master, "llm"):
            llm = master.llm
        else:
            from nexus.common.ollama_client import OllamaClient
            from nexus.common import ai_layer
            llm = OllamaClient()
            ai_layer.apply_to_client(llm)
        ping = llm.ping()
        reachable = bool(ping.get("reachable") or ping.get("ok"))
        model = llm.pick_model() if reachable else None
        err = ping.get("error")
    except Exception as e:
        err = str(e)
    ok = bool(reachable and model)
    return {
        "modality": "text",
        "status": STATUS_DONE if ok else STATUS_PARTIAL,
        "works": ok,
        "impl": "nexus.common.ollama_client.chat + /api/chat",
        "model": model,
        "evidence": ["POST /api/chat", "POST /api/chat/stream", "ai_layer keep_alive=0"],
        "gaps": [] if ok else ["ollama_unreachable_or_no_model"],
        "error": err,
    }


def _image_status(master: Any = None) -> dict[str, Any]:
    """Vision/image analyze via local llava — on-demand, never pull."""
    vision_model = None
    keep_alive = "0"
    never_pull = True
    try:
        from nexus.common import ai_layer
        from nexus.common.ollama_client import OllamaClient
        cfg = ai_layer.load_config()
        keep_alive = str(ai_layer.keep_alive_value(cfg))
        never_pull = bool(ai_layer.never_pull(cfg))
        llm = master.llm if (master is not None and hasattr(master, "llm")) else OllamaClient()
        if master is None:
            ai_layer.apply_to_client(llm)
        vision_model = ai_layer.resolve_vision_model(llm)
    except Exception:
        try:
            from nexus.common.ollama_client import OllamaClient
            llm = master.llm if (master is not None and hasattr(master, "llm")) else OllamaClient()
            vision_model = llm.has_vision()
        except Exception:
            vision_model = None
    ok = bool(vision_model)
    return {
        "modality": "image",
        "status": STATUS_DONE if ok else STATUS_UNKNOWN,
        "works": ok,
        "impl": "ollama_client.vision + media.image.analyze + /api/vision",
        "model": vision_model,
        "keep_alive": keep_alive,
        "on_demand_only": True,
        "never_pull": never_pull,
        "evidence": [
            "POST /api/vision",
            "POST /api/screen/analyze (frame upload)",
            "llava local only — no auto-pull",
        ],
        "gaps": [] if ok else ["no_vision_model_installed"],
        "note": "Vision loads on request and unloads (keep_alive=0). 16GB RAM policy.",
    }


def _audio_status(master: Any = None) -> dict[str, Any]:
    """TTS (edge-tts) + STT (faster-whisper or browser)."""
    tts_ok = False
    stt_info: dict[str, Any] = {}
    speak_pc = False
    try:
        if master is not None and hasattr(master, "voice"):
            st = master.voice.status()
            tts_ok = bool((st.get("tts") or {}).get("edge_tts"))
            stt_info = dict(st.get("stt") or {})
            speak_pc = bool(st.get("speak_pc"))
        else:
            from nexus.voice import tts as tts_mod
            from nexus.voice import stt as stt_mod
            tts_ok = bool(tts_mod.available())
            stt_info = stt_mod.status()
            speak_pc = True
    except Exception as e:
        stt_info = {"error": str(e)}
    stt_server = bool(stt_info.get("available"))
    # DONE if TTS works; STT server optional (browser fallback documented)
    if tts_ok and stt_server:
        status = STATUS_DONE
        gaps = ["vad_stub"]
    elif tts_ok:
        status = STATUS_PARTIAL
        gaps = ["server_stt_unavailable_use_browser_webspeech", "vad_stub"]
    else:
        status = STATUS_PARTIAL
        gaps = ["tts_unavailable", "vad_stub"]
    return {
        "modality": "audio",
        "status": status,
        "works": bool(tts_ok or stt_server),
        "impl": "voice.tts (edge-tts) + voice.stt (faster-whisper|browser) + /api/voice/*",
        "tts": {"edge_tts": tts_ok, "endpoint": "POST /api/voice/tts"},
        "stt": {
            **stt_info,
            "endpoint": "POST /api/voice/stt",
            "turn": "POST /api/voice/turn",
        },
        "speak_pc": speak_pc,
        "vad": {"available": False, "status": STATUS_STUB, "file": "nexus/voice/vad.py"},
        "evidence": [
            "GET /api/voice/status",
            "POST /api/voice/tts",
            "POST /api/voice/stt",
            "POST /api/voice/turn",
        ],
        "gaps": gaps,
        "funnel": "NOT public — Gateway deny /api/voice*",
    }


def _camera_status(_master: Any = None) -> dict[str, Any]:
    """No server camera capture pipeline in this tree."""
    return {
        "modality": "camera",
        "status": STATUS_UNKNOWN,
        "works": False,
        "impl": None,
        "evidence": [],
        "gaps": [
            "no_camera_capture_module",
            "no_/api/camera",
            "browser_getUserMedia_not_wired_as_modality",
        ],
        "note": "UNKNOWN — not stubbed as DONE. Client webcam would need explicit wire.",
    }


def _video_status(_master: Any = None) -> dict[str, Any]:
    """Local video library + URL play hints — not capture/stream encode."""
    count = None
    try:
        from nexus.media.video_library import VideoLibrary
        r = VideoLibrary().list()
        count = r.get("count")
    except Exception as e:
        return {
            "modality": "video",
            "status": STATUS_PARTIAL,
            "works": False,
            "impl": "media.video_library + media.player",
            "error": str(e),
            "gaps": ["video_library_error", "no_video_capture", "no_video_vision_pipeline"],
        }
    return {
        "modality": "video",
        "status": STATUS_PARTIAL,
        "works": True,  # library/list works
        "impl": "media.video_library + /api/videos + media.player",
        "library_count": count,
        "evidence": ["GET /api/videos", "GET /api/media", "VideoLibrary.list"],
        "gaps": [
            "no_video_frame_vision",
            "no_video_capture",
            "no_transcode_pipeline",
        ],
        "note": "Playback/library only — not camera/video recording.",
    }


def _screen_share_status(_master: Any = None) -> dict[str, Any]:
    """Screen Share live streaming = UNKNOWN.
    Screen frame analyze (client upload + llava) = separate capability (PARTIAL/DONE via image).
    """
    return {
        "modality": "screen_share",
        "status": STATUS_UNKNOWN,
        "works": False,
        "impl": None,
        "related": {
            "screen_analyze": {
                "status": STATUS_DONE,
                "endpoint": "POST /api/screen/analyze",
                "note": "Client uploads a frame (b64); llava describes it. NOT live Screen Share.",
                "keep_alive": "0",
                "saves_to": "workspace/screens/",
            }
        },
        "evidence": [
            "POST /api/screen/analyze exists (upload+vision only)",
            "modules_map previously claimed screen=true — dishonest for live share",
        ],
        "gaps": [
            "no_live_screen_capture_pipeline",
            "no_webrtc_screen_share",
            "no_/api/screen/share",
        ],
        "note": "UNKNOWN — do not mark DONE. Analyze-upload ≠ Screen Share.",
    }


def modality_matrix(master: Any = None) -> list[dict[str, Any]]:
    return [
        _text_status(master),
        _image_status(master),
        _audio_status(master),
        _camera_status(master),
        _video_status(master),
        _screen_share_status(master),
    ]


def multimodal_status(master: Any = None) -> dict[str, Any]:
    rows = modality_matrix(master)
    by = {r["modality"]: r["status"] for r in rows}
    done = sum(1 for r in rows if r["status"] == STATUS_DONE)
    partial = sum(1 for r in rows if r["status"] == STATUS_PARTIAL)
    unknown = sum(1 for r in rows if r["status"] == STATUS_UNKNOWN)
    stub = sum(1 for r in rows if r["status"] == STATUS_STUB)
    # Phase-level: never PASS if claiming screen_share DONE; PARTIAL when core text+image+audio work
    core_ok = (
        by.get("text") == STATUS_DONE
        and by.get("image") == STATUS_DONE
        and by.get("audio") in (STATUS_DONE, STATUS_PARTIAL)
    )
    return {
        "success": True,
        "ok": True,
        "layer": "nexus_multimodal",
        "phase": 10,
        "local_only": True,
        "funnel_exposed": False,
        "paid_core_deps": False,
        "never_pull": True,
        "vision_policy": {
            "model": "llava:latest (if installed)",
            "keep_alive": "0",
            "on_demand_only": True,
            "never_pull": True,
            "reason": "16GB RAM — unload after request",
        },
        "counts": {
            "DONE": done,
            "PARTIAL": partial,
            "UNKNOWN": unknown,
            "STUB": stub,
            "total": len(rows),
        },
        "by_modality": by,
        "modalities": rows,
        "core_text_image_audio_ok": core_ok,
        "honest_rule": "Only DONE if smokeable. Stubs/missing = UNKNOWN. Screen Share ≠ analyze.",
    }
