"""Text modality — local chat via AI layer / Ollama."""
from __future__ import annotations
from typing import Any


def status(master: Any = None) -> dict:
    from nexus.multimodal.registry import _text_status
    return _text_status(master)


def chat(master: Any, messages: list[dict], *, model: str | None = None) -> dict:
    """Delegate to OllamaClient.chat (keep_alive from AI layer)."""
    return master.llm.chat(messages, model=model)
