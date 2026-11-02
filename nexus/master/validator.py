"""Antwort-Validierung — DE, keine leeren Blobs, kein Fake-News-Essay."""
from __future__ import annotations
import re

_FAKE_YT_LIST = re.compile(
    r"(hier\s+(sind|ist)\s+(einige|ein\s+paar|meine)\s+(youtube\s+)?(videos?|treffer|empfehlungen)|"
    r"empfohlene\s+videos?|top\s*\d+\s+youtube)",
    re.I,
)

_FAKE_NEWS_MARKERS = re.compile(
    r"(knowledge\s*cutoff|mein\s+wissen\s+(endet|reicht)|"
    r"erste\s+anzeichen\s+(von\s+)?nexus|"
    r"exoplanet(?:en)?\s+202|"
    r"aktuell(?:e|en)?\s+entwicklungen\s+und\s+neuigkeiten)",
    re.I,
)


def validate_reply(reply: str, *, intent: str = "chat") -> dict:
    text = (reply or "").strip()
    if not text:
        return {
            "ok": False,
            "reply": "Keine Antwort erzeugt. Bitte erneut versuchen oder Ollama prüfen.",
            "fixed": True,
        }
    # Chat ohne YouTube-Tool: erfundene Video-Listen verwerfen
    if intent == "chat" and _FAKE_YT_LIST.search(text) and ("youtube" in text.lower() or "video" in text.lower()):
        return {
            "ok": False,
            "reply": (
                "BLOCKED: Keine erfundenen YouTube-Titel. "
                "Bitte «suche auf youtube <Thema>» — dann echte Live-Treffer."
            ),
            "fixed": True,
            "intent": intent,
        }
    # Chat ohne Research: lange Fake-News / Cutoff-Theater verwerfen
    if intent == "chat" and _FAKE_NEWS_MARKERS.search(text) and len(text) > 400:
        return {
            "ok": False,
            "reply": (
                "BLOCKED: Keine erfundenen Welt-News. "
                "Bitte «suche <Thema>» oder eine aktuelle News-Frage stellen — dann Live-Web."
            ),
            "fixed": True,
            "intent": intent,
        }

    # Bau-Intent: Chat-Codebloecke / Pseudocode-Labern ohne Tool-Marker ablehnen
    if intent in ("build", "shell", "run_script", "execute"):
        has_tool_marker = bool(re.search(r"(<tool_call>|```tool\b|TOOL_RESULT)", text, re.I))
        has_code_dump = bool(re.search(
            r"```(?:python|py|javascript|js|typescript|ts|tsx|powershell|bash|sh)\b",
            text, re.I,
        ))
        if has_code_dump and not has_tool_marker:
            return {
                "ok": False,
                "reply": (
                    "STOP (build_no_tools_laber): Bauauftrag braucht echte Tools "
                    "(write_file/powershell/run_script), keine Chat-Codebloecke. "
                    "Bitte erneut — Tool-Pfad."
                ),
                "fixed": True,
                "intent": intent,
            }

    # fluff_trim: plain chat essays without tool markers → keep tight
    if intent == "chat" and len(text) > 900 and not re.search(
        r"(TOOL_RESULT|http://|https://|BLOCKED:|G:\\|workspace/)", text
    ):
        # keep first ~3 paragraphs / 700 chars
        parts = re.split(r"\n\s*\n", text)
        trimmed = "\n\n".join(parts[:3]).strip()
        if len(trimmed) > 700:
            trimmed = trimmed[:700].rsplit(" ", 1)[0] + " …"
        if len(trimmed) < len(text):
            text = trimmed + "\n(gekürzt — bei Bedarf nachfragen)"

    if len(text) > 12000:
        text = text[:12000] + "\n… (gekürzt)"
    return {"ok": True, "reply": text, "fixed": False, "intent": intent}
