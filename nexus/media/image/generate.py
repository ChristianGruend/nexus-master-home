from __future__ import annotations

def generate_stub(prompt: str) -> dict:
    return {
        "success": False,
        "stub": True,
        "reply": f"Bildgenerierung nicht lokal verdrahtet. Prompt gespeichert als Idee: {prompt[:200]}",
    }
