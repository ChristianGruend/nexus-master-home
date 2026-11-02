"""STT: Server via faster-whisper wenn verfuegbar, sonst Browser WebSpeech."""
from __future__ import annotations
import io
import tempfile
from pathlib import Path

_MODEL = None
_MODEL_NAME = None
_MODELS: dict = {}  # je Größe genau ein geladenes Modell (kein Neuladen beim Wechsel tiny/base)
ALLOWED_SIZES = ("tiny", "base")  # nur lokal vorhandene Modelle, kein automatischer Download größerer
_IMPORT_ERROR = None

try:
    from faster_whisper import WhisperModel  # type: ignore
    _HAS_FW = True
except Exception as e:  # pragma: no cover
    WhisperModel = None  # type: ignore
    _HAS_FW = False
    _IMPORT_ERROR = str(e)


def status() -> dict:
    avail = bool(_HAS_FW)
    return {
        "available": avail,
        "mode": "faster_whisper" if avail else "browser_webspeech",
        "model": _MODEL_NAME or ("tiny" if avail else None),
        "loaded": _MODEL is not None,
        "note": (
            "Server-STT via faster-whisper (POST /api/voice/stt)."
            if avail
            else "Spracherkennung clientseitig (Web Speech API). Server-STT nicht installiert."
        ),
        "import_error": _IMPORT_ERROR,
    }


def _ensure_model(model_size: str = "tiny"):
    global _MODEL, _MODEL_NAME
    if not _HAS_FW:
        raise RuntimeError("faster_whisper_not_installed")
    if model_size not in ALLOWED_SIZES:
        model_size = "tiny"
    if model_size not in _MODELS:
        # CPU int8 — schnell genug fuer kurze Clips, kein GPU noetig
        _MODELS[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
    _MODEL, _MODEL_NAME = _MODELS[model_size], model_size
    return _MODEL


def transcribe_bytes(data: bytes, filename: str = "audio.wav", language: str = "de", model_size: str = "tiny",
                     initial_prompt: str | None = None) -> dict:
    if not data:
        return {"success": False, "error": "empty_audio", "reply": "Keine Audiodaten."}
    if not _HAS_FW:
        return {
            "success": False,
            "error": "stt_unavailable",
            "mode": "browser_webspeech",
            "reply": "Server-STT nicht verfuegbar — bitte Web Speech im Browser nutzen.",
        }
    suffix = Path(filename or "audio.wav").suffix or ".wav"
    try:
        model = _ensure_model(model_size)
    except Exception as e:
        return {
            "success": False,
            "error": f"model_load:{e}",
            "reply": f"Whisper-Modell konnte nicht geladen werden: {e}",
        }
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            tf.write(data)
            tmp_path = tf.name
        segments, info = model.transcribe(tmp_path, language=language or "de", beam_size=1,
                                          initial_prompt=initial_prompt or None)
        parts = []
        for seg in segments:
            t = (seg.text or "").strip()
            if t:
                parts.append(t)
        text = " ".join(parts).strip()
        return {
            "success": True,
            "text": text,
            "language": getattr(info, "language", language),
            "duration": getattr(info, "duration", None),
            "model": _MODEL_NAME,
            "mode": "faster_whisper",
            "reply": text or "(kein Text erkannt)",
        }
    except Exception as e:
        return {"success": False, "error": str(e), "reply": f"STT-Fehler: {e}"}
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


def transcribe_file(path: str | Path, language: str = "de", model_size: str = "tiny") -> dict:
    p = Path(path)
    if not p.is_file():
        return {"success": False, "error": "file_not_found"}
    return transcribe_bytes(p.read_bytes(), filename=p.name, language=language, model_size=model_size)
