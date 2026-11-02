"""LEITBILD von NEXUS Master Home als pruefbare Haltung (Studio, Mail, All). Reine Funktionen, keine Seiteneffekte."""
from __future__ import annotations
import ipaddress
import re
from urllib.parse import urlparse

LEITBILD = ("NEXUS Master Home soll zeigen, dass Mensch und Maschine zusammen stark sind, und die Welt fuer jeden besser machen. "
            "Es verbindet Mensch und Maschine und auch Menschen untereinander. Es vermittelt die richtigen Werte: Ehrlichkeit, Schutz, "
            "Hilfsbereitschaft, Fuersorge, gute Zusammenarbeit und Freundschaft. Es bleibt kostenlos und lokal zuerst.")

RISKY_EXT = {".exe", ".bat", ".cmd", ".ps1", ".scr", ".js", ".vbs", ".msi", ".jar", ".com", ".pif", ".hta", ".lnk", ".reg", ".apk"}
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "cutt.ly", "ow.ly", "rb.gy"}
APPROVAL_OPS = {"delete", "move", "overwrite", "rename", "export", "share", "send"}


def check_download_url(url: str) -> dict:
    """NEXUS All: Schutz vor dubiosen Downloads/Seiten. ok=False -> blockieren, warnings -> nachfragen."""
    warnings, u = [], urlparse(str(url or "").strip())
    if u.scheme not in ("http", "https"):
        return {"ok": False, "warnings": ["nur http/https erlaubt"]}
    host = (u.hostname or "").lower()
    if not host:
        return {"ok": False, "warnings": ["keine Domain"]}
    if u.scheme == "http":
        warnings.append("unverschluesselt (http)")
    try:
        ipaddress.ip_address(host); warnings.append("IP-Adresse statt Domain")
    except ValueError:
        pass
    if "xn--" in host:
        warnings.append("Punycode-Domain (moegliche Taeuschung)")
    if host in SHORTENERS:
        warnings.append("Link-Verkuerzer: Ziel unbekannt")
    path = u.path.lower()
    ext = path[path.rfind("."):] if "." in path.rsplit("/", 1)[-1] else ""
    if ext in RISKY_EXT:
        warnings.append(f"ausfuehrbare Datei ({ext})")
    return {"ok": True, "warnings": warnings}


def ai_label(meta: dict | None, *, generator: str) -> dict:
    """Studio: KI-erzeugte/-veraenderte Inhalte ehrlich kennzeichnen."""
    out = dict(meta or {})
    out.update({"ai_generated": True, "ai_generator": str(generator), "label": "KI-erzeugt"})
    return out


def requires_approval(op: str) -> bool:
    """Studio/All/Mail: Dateioperationen und Senden nur mit Freigabe."""
    return str(op or "").strip().lower() in APPROVAL_OPS


def send_allowed(draft: dict | None) -> bool:
    """Mail: nichts wird ohne ausdrueckliche Freigabe gesendet."""
    return bool(draft) and draft.get("approved") is True


_URGENT = re.compile(r"\b(sofort|dringend|gesperrt|verifizieren|bestaetigen sie|passwort|kennwort|tan|zahlung|urgent|verify|suspended|password)\b", re.I)
_LINK = re.compile(r"https?://[^\s\"'<>]+", re.I)


def mail_phishing_hints(sender: str, subject: str = "", body: str = "", attachments: list | None = None) -> list[str]:
    """Mail: Hinweise auf Phishing/Spam (Warnung, keine Entscheidung)."""
    hints, s = [], str(sender or "")
    m = re.match(r'\s*"?([^"<]*)"?\s*<([^>]+)>', s)
    name, addr = (m.group(1).strip(), m.group(2).strip().lower()) if m else ("", s.strip().lower())
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    named = re.search(r"([a-z0-9-]+\.[a-z]{2,})", name.lower())
    if named and domain and not domain.endswith(named.group(1)):
        hints.append(f"Anzeigename nennt {named.group(1)}, Absender ist {domain}")
    text = f"{subject} {body}"
    if _URGENT.search(text):
        hints.append("Druck/Passwort/Zahlung im Text")
    for link in _LINK.findall(body or ""):
        r = check_download_url(link)
        if not r["ok"] or r["warnings"]:
            hints.append(f"verdaechtiger Link: {link[:80]}")
    for a in attachments or []:
        n = str(a).lower()
        if any(n.endswith(e) for e in RISKY_EXT):
            hints.append(f"gefaehrlicher Anhang: {a}")
    return hints
