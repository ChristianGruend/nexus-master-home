from __future__ import annotations


class CodeRuntime:
    """Legacy snippet interface. Generated code must use the registered isolated path."""

    def run(self, code: str, timeout: int | None = None) -> dict:
        return {
            "success": False, "status": "BLOCKED", "error": "CODE_EXECUTION_DISABLED",
            "returncode": None, "stdout": "", "stderr": "",
            "reply": "Codeausführung ist deaktiviert. Erstellung und statische Prüfung sind über den Hermes-Codeauftrag verfügbar.",
        }
