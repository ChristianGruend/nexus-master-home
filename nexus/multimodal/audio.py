"""Audio modality — TTS / STT facade over nexus.voice."""
from __future__ import annotations
from typing import Any


def status(master: Any = None) -> dict:
    from nexus.multimodal.registry import _audio_status
    return _audio_status(master)


def tts(master: Any, text: str) -> dict:
    return master.voice.tts(text)


def stt_bytes(data: bytes, *, filename: str = "audio.wav", language: str = "de", model_size: str = "tiny") -> dict:
    from nexus.voice import stt as stt_mod
    return stt_mod.transcribe_bytes(data, filename=filename, language=language, model_size=model_size)
