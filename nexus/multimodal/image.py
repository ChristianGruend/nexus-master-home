"""Image / Vision modality — llava on-demand, keep_alive=0, never pull."""
from __future__ import annotations
from typing import Any

from nexus.media.image.analyze import analyze_image


def status(master: Any = None) -> dict:
    from nexus.multimodal.registry import _image_status
    return _image_status(master)


def analyze(master: Any, image_bytes: bytes, prompt: str | None = None) -> dict:
    """Run vision via existing llm.vision (uses has_vision / llava; chat applies keep_alive=0)."""
    p = prompt or "Beschreibe das Bild auf Deutsch."
    # Prefer AI-layer resolved model if present
    model = None
    try:
        from nexus.common import ai_layer
        model = ai_layer.resolve_vision_model(master.llm)
    except Exception:
        model = None
    if model:
        return master.llm.vision(p, image_bytes, model=model)
    return analyze_image(master.llm, image_bytes, p)
