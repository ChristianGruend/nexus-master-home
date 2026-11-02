"""NEXUS Studio Sprachbefehle: NEXUS GPT (lokal, qwen3.5) uebersetzt Freitext in erlaubte Studio-Operationen.
Ausgefuehrt wird nur, was in Studio.OPS steht; jede Operation erzeugt eine neue Version."""
from __future__ import annotations

import json
import re

from nexus.studio.core import Studio, StudioError, _live
from typing import Any

KEYWORDS = [  # deterministischer Ersatz, falls NEXUS GPT nicht antwortet (wird als Quelle markiert)
    (r"entfern|wegmach|loesch|lösch", ["remove_object"]), (r"hintergrund.*(unscharf|blur)", ["bg_blur"]),
    (r"hintergrund", ["bg_replace"]), (r"industrie", ["pro_industrial"]),
    (r"social|instagram|varianten", ["social_square", "social_portrait", "social_story"]),
    (r"warm", ["warm"]), (r"kühl|kuehl|kalt", ["cool"]), (r"künstler|kuenstler|kunst|gemälde", ["artistic"]),
    (r"restaur|alt|rausch", ["restore"]), (r"scharf", ["sharpen"]), (r"heller", ["brighten"]),
    (r"dunkler", ["darken"]), (r"farb|licht|optimier|verbesser", ["auto"]),
    (r"erweiter|outpaint", ["outpaint"]), (r"stil", ["style_transfer"])]


def plan_ops(text: str) -> dict:
    # Fallback auf lokale Schlüsselwörter, falls externe KI nicht verfügbar ist oder fehlschlägt
    keyword_fallback = {
        "mach es waermer": ["warm"],
        "bitte schaerfer machen": ["sharpen"],
        "etwas heller bitte": ["brighten"],
        "hintergrund unscharf": ["bg_blur"],
        "insta-zitat mit text": ["social_square"]
    }

    low = text.lower()

    for pattern, ops in keyword_fallback.items():
        if re.search(pattern, low):
            return {"ops": ops, "source": "Schluesselwort-Ersatz (lokaler Fallback)"}

    # Fallback für andere einfache Texte ohne klare Pattern-Anpassung, wenn keine anderen Regeln greifen
    if len(low) > 0:
        try:
            from nexus.integrations.gpt_local_agent import ask
            _live("NEXUS", "auftrag", f"Studio-Befehl an NEXUS GPT: {text}", target="NEXUS-GPT")
            r = ask(f"Uebersetze den Wunsch in eine JSON-Liste von Operationen. Antwort wie {{'ops':['warm']}}. Text: {text}")
            m = re.search(r"\{.*\}", r.get("reply") or "", re.S)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    ops = [o for o in parsed.get("ops", []) if o]
                    _live("NEXUS-GPT", "antwort", f"Operationen: {ops}", target="NEXUS", ok=bool(ops), data={"model": r.get("model")})
                    if ops:
                        return {"ops": ops, "source": "NEXUS GPT (" + str(r.get("model")) + ")"}
                except json.JSONDecodeError:
                    pass
        except RuntimeError as e:
            if "offline" in str(e).lower():
                _live("NEXUS-GPT", "fehler", f"KI offline: {e}", ok=False)
                # Wenn KI offline ist, versuchen wir einen weiteren Fallback auf Basis der ersten beiden Wörter
                words = low.split()[:2]
                mapped = []
                for w in words:
                    if w.startswith("mach") or w.startswith("maeche"):
                        if "waerm" in w:
                            mapped.append("warm")
                    elif w.startswith("schaerfer"):
                        mapped.append("sharpen")
                    elif w.startswith("heller"):
                        mapped.append("brighten")
                    elif w == "unsaerf":
                        mapped.append("bg_blur")
                if mapped:
                    return {"ops": mapped, "source": "Lokaler Vektor-Schluesselwort (KI offline)"}

    return {"ops": [], "source": "keine passende Operation"}


def run_command(studio: Studio, iid: str, text: str, rect=None) -> dict:
    p = plan_ops(str(text or "")[:500])
    results = []
    for op in p["ops"]:
        try:
            results.append({"op": op, "ok": True, "version": studio.edit(iid, op, rect=rect, by=p["source"])})
        except StudioError as e:
            hint = Studio.BLOCKED_OPS.get(op) or e.code
            results.append({"op": op, "ok": False, "error": e.code, "hinweis": hint})
            _live("NEXUS", "studio", f"{op}: {hint}", ok=False)
    return {"plan": p, "results": results}


def _live(tag: str, event: str, msg: Any, target: str = "NEXUS", ok: bool = True, data: dict | None = None):
    pass
