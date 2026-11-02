from __future__ import annotations

class Generator:
    """Text-Generierung über Ollama (Wrapper)."""

    def __init__(self, llm) -> None:
        self.llm = llm

    def text(self, prompt: str, system: str | None = None) -> dict:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return self.llm.chat(msgs)
