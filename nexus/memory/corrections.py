"""Eddie-Korrekturen / Praeferenzen — persist + Kontext-Injection."""
from __future__ import annotations
import json
import re
from datetime import datetime
from pathlib import Path
from nexus.common.paths import APEX, ensure_dirs

STORE = APEX / "eddie_corrections.json"

# Trigger-Phrasen (Deutsch + kurz EN)
_CORRECTION_HEAD = re.compile(
    r"^\s*(?:"
    r"besser\s+so|nicht\s+so|lieber\s+so|"
    r"immer(?:\s+so)?|nie(?:\s+wieder)?(?:\s+so)?|"
    r"ab\s+jetzt|korrektur|korrigiere|"
    r"praeferenz|präferenz|preference|"
    r"mach\s+das\s+(?:immer|nie)|bitte\s+immer|bitte\s+nie|"
    r"do\s+always|never\s+do"
    r")\b",
    re.I,
)

_INLINE_HINT = re.compile(
    r"\b("
    r"besser\s+so|nicht\s+so|immer\s+so|nie\s+so|nie\s+wieder|"
    r"ab\s+jetzt|korrektur\s*:|korrigiere\s*:"
    r")\b",
    re.I,
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_correction_message(message: str) -> bool:
    raw = (message or "").strip()
    low = raw.lower()

    if not raw or len(raw) < 6:
        return False

    # Lange Bau-, Diagnose- und Arbeitsauftraege duerfen nicht
    # automatisch als dauerhafte Korrektur gespeichert werden.
    if len(raw) > 600:
        return False

    # Mehrzeilige strukturierte Arbeitsauftraege ausschliessen.
    work_markers = (
        "schritt 1",
        "schritt 2",
        "aufgabe:",
        "ziel:",
        "ausgabe:",
        "result:",
        "tools:",
        "beginne jetzt",
        "bauauftrag",
        "diagnose",
        "read_only",
    )
    if raw.count("\n") >= 4 and any(x in low for x in work_markers):
        return False

    if _CORRECTION_HEAD.search(raw):
        return True

    # Inline nur bei einer kurzen, klaren Benutzerregel.
    if len(raw) <= 300 and _INLINE_HINT.search(raw):
        explicit = (
            "besser so",
            "nicht so",
            "ab jetzt",
            "korrektur",
            "korrigiere",
            "nie wieder",
            "mach das immer",
            "mach das nie",
            "lieber so",
        )
        return any(k in low for k in explicit)

    return False


def _classify(text: str) -> str:
    raw = (text or "").strip()
    low = raw.lower()

    if "präferenz" in low or "praeferenz" in low or "preference" in low or "lieber so" in low:
        return "prefer"

    if re.search(r"\bnie(?:\s+wieder)?\b", low) or "nicht so" in low:
        return "never"

    # 'always' nur fuer kurze, konkrete Dauerregeln.
    # Ein beliebiges Vorkommen von 'immer' oder 'ab jetzt'
    # reicht ausdrücklich nicht mehr.
    explicit_always = (
        low.startswith("ab jetzt ")
        or low.startswith("immer ")
        or "mach das immer" in low
        or "bitte immer" in low
    )

    if explicit_always and len(raw) <= 300 and raw.count("\n") <= 3:
        return "always"

    return "note"

class CorrectionsStore:
    def __init__(self) -> None:
        ensure_dirs()
        self._data = self._load()

    def _load(self) -> list:
        if not STORE.exists():
            return []
        try:
            return json.loads(STORE.read_text(encoding="utf-8")).get("items", [])
        except Exception:
            return []

    def _save(self) -> None:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(
            json.dumps({"updated": _now(), "items": self._data}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def add(self, text: str, raw: str | None = None, kind: str | None = None) -> dict:
        text = (text or "").strip()
        if not text:
            return {"success": False, "error": "leer", "reply": "Welche Korrektur soll ich speichern?"}
        kind = kind or _classify(text)
        item = {
            "id": f"C-{len(self._data) + 1:04d}",
            "kind": kind,
            "text": text,
            "raw": (raw or text).strip(),
            "created": _now(),
        }
        self._data.append(item)
        self._save()
        return {
            "success": True,
            "item": item,
            "reply": f"Korrektur gespeichert ({kind}): {text}",
        }

    def list(self, limit: int = 40) -> list:
        return list(reversed(self._data))[:limit]

    def context_snippets(self, limit: int = 12) -> str:
        items = list(reversed(self._data))[:limit]
        if not items:
            return ""
        lines = []
        for i in items:
            k = i.get("kind") or "note"
            lines.append(f"- [{k}] {i.get('text', '')}")
        return "Eddie-Korrekturen / Praeferenzen (verbindlich beachten):\n" + "\n".join(lines)


def project_knowledge_snippet(limit_chars: int = 3500) -> str:
    """Liest PROJECT_KNOWLEDGE.md fuer Build-Kontext."""
    ensure_dirs()
    path = APEX / "PROJECT_KNOWLEDGE.md"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if not text:
        return ""
    if len(text) > limit_chars:
        text = text[:limit_chars] + "\n…"
    return "Projekt-Wissen (verbindlich vor Builds lesen):\n" + text

