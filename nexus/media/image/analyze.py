from __future__ import annotations

def analyze_image(llm, image_bytes: bytes, prompt: str = "Beschreibe das Bild auf Deutsch.") -> dict:
    return llm.vision(prompt, image_bytes)
