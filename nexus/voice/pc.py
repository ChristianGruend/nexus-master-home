"""PC-Steuerung per Sprache (NEXUSPhone-Anruf) – nur eine feste Positivliste, jede Aktion mit Nachweis.

Erlaubt ohne Rückfrage: bekannte Apps öffnen, Webseiten öffnen, im Internet suchen, PC-Lautstärke,
PC-Status vorlesen (nur lesend). Mit Bestätigung ("ja"): App am PC schließen.
Nicht per Sprache: Konsolen (cmd/PowerShell), beliebige Programme oder Pfade, Löschen, Herunterfahren.
Ausgeführt wird über die vorhandenen OSActions des NEXUS-Core; NEXUS meldet nur nachgewiesenen Erfolg.
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote_plus

PC_CONTEXT = re.compile(r"\b(?:am|auf dem|beim|im) (?:pc|computer|rechner|laptop|desktop)\b|\b(?:pc|computer|rechner)\b")

# Apps, die per Sprache geöffnet/geschlossen werden dürfen (Schlüssel aus OSActions.APP_MAP/ALIASES).
SAFE_APPS = {"chrome", "edge", "firefox", "brave", "notepad", "rechner", "calc", "explorer", "spotify", "teams",
             "telegram", "vlc", "wmplayer", "musik", "groove", "einstellungen", "settings", "fotos", "kamera",
             "discord", "steam", "code", "vscode", "cursor", "youtube", "gmail", "kalender", "calendar", "drive",
             "maps", "facebook", "whatsapp", "tiktok"}
SPOKEN_ALIASES = {"google chrome": "chrome", "browser": "chrome", "internet": "chrome", "editor": "notepad",
                  "notizblock": "notepad", "taschenrechner": "rechner", "dateien": "explorer", "ordner": "explorer",
                  "datei explorer": "explorer", "visual studio code": "code", "vs code": "code", "you tube": "youtube",
                  "whats app": "whatsapp", "e mail": "gmail", "mail": "gmail", "google maps": "maps"}
BLOCKED = re.compile(r"\b(cmd|eingabeaufforderung|konsole|terminal|powershell|shell|regedit|registry|"
                     r"herunter\w*|runterfahren|shutdown|abschalten|ausschalten|schalte\w* \w+ ?\w* aus|"
                     r"neustarten|neu starten|loeschen|formatieren|deinstallieren)\b")
CLOSE_TTL_S = 60


def _norm(text: str) -> str:
    t = str(text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9. ]+", " ", t)).strip()


CTX = r"\b(?:am|an|an dem|auf dem|beim|im) (?:pc|computer|rechner|laptop|desktop)\b"
# Typische Verhörer der Spracherkennung für App-Namen.
HEARD = {"krom": "chrome", "chrom": "chrome", "grom": "chrome", "krome": "chrome", "crome": "chrome", "kroem": "chrome",
         "note pad": "notepad", "notpad": "notepad", "spotifei": "spotify", "spotty fei": "spotify", "edtor": "editor",
         "fire fox": "firefox", "dis cord": "discord", "v s code": "code", "wie es code": "code"}


def _strip_ctx(t: str) -> str:
    t = re.sub(CTX, " ", t)
    t = re.sub(r"\b(?:bitte|mal|mir|doch|kannst du|koenntest du|nexus|hermes|hey|hallo|die app|das programm|die|das|den)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip(" .")


def _app_key(name: str) -> str | None:
    import difflib
    n = name.strip(" .")
    n = HEARD.get(n, n)
    n = SPOKEN_ALIASES.get(n, n)
    if n in SAFE_APPS:
        return n
    n2 = n.replace(" ", "")
    if n2 in SAFE_APPS:
        return n2
    # Ähnlichkeitssuche nur innerhalb der Positivliste (nie beliebige Programme).
    names = sorted(SAFE_APPS | set(SPOKEN_ALIASES) | set(HEARD))
    best = difflib.get_close_matches(n2, [x.replace(" ", "") for x in names], n=1, cutoff=0.75)
    if best:
        hit = next(x for x in names if x.replace(" ", "") == best[0])
        hit = HEARD.get(hit, hit)
        return SPOKEN_ALIASES.get(hit, hit) if SPOKEN_ALIASES.get(hit, hit) in SAFE_APPS else None
    return None


DOMAIN = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:de|com|org|net|io|eu|info|tv)$")


def parse(text: str) -> dict | None:
    """Erkennt PC-Befehle. Rückgabe {op, ...} oder None (dann kein PC-Befehl)."""
    t = _norm(text)
    # Zusammengezogene Wörter der Spracherkennung trennen ("schliesskrom" -> "schliess krom"),
    # gesprochene Domains zusammensetzen ("heise punkt de" -> "heise.de").
    t = re.sub(r"\b(schliess|oeffne)(?!e\b|t\b|en\b|n\b)([a-z]{3,})\b", r"\1 \2", t)
    t = re.sub(r"\s+punkt\s+(de|com|org|net|io|eu|info|tv)\b", r".\1", t)
    if not PC_CONTEXT.search(t) and not re.search(r"\b(?:im internet|im browser|googl\w*)\b", t):
        return None
    if BLOCKED.search(t):
        return {"op": "blocked", "what": BLOCKED.search(t).group(1)}
    if re.search(r"\b(status|auslastung|wie geht es (?:dem|meinem) (?:pc|computer|rechner)|arbeitsspeicher|cpu)\b", t):
        return {"op": "status"}
    if re.search(r"\b(lauter|leiser|stumm|ton aus|ton an|lautstaerke)\b", t):
        if re.search(r"\b(stumm|ton aus)\b", t):
            return {"op": "volume", "action": "mute"}
        if re.search(r"\bton an\b", t):
            return {"op": "volume", "action": "unmute"}
        return {"op": "volume", "action": "down" if "leiser" in t or "runter" in t else "up"}
    # Suche: Begriff mit Umlauten aus dem Original (nur Satzzeichen entfernt, Ortsangaben für den PC raus).
    orig = re.sub(r"\s+", " ", re.sub(r"[^\wäöüÄÖÜß. ]+", " ", str(text or ""))).strip()
    orig = re.sub(r"\b(?:am|auf dem|beim|im) (?:pc|computer|rechner|laptop|desktop)\b|\b(?:im internet|im browser|bei google)\b",
                  " ", orig, flags=re.I)
    m = re.search(r"\b(?:such\w*|googl\w*|recherchier\w*)\b\s+(?:bitte\s+|mal\s+|mir\s+)*(?:nach\s+)?(.{2,80})$", orig, re.I)
    if m:
        q = re.sub(r"\s+", " ", m.group(1)).strip(" .")
        if q:
            return {"op": "search", "query": q}
    m = re.search(r"\b(?:schliess\w*|beend\w*|mach\w*)\b (.{2,40}?)(?: zu)?$", t)
    if m and re.search(r"\b(schliess|beend|zu$)", t):
        key = _app_key(_strip_ctx(m.group(1)))
        return {"op": "close_app", "app": key, "spoken": _strip_ctx(m.group(1))}
    m = re.search(r"\b(?:oeffne\w*|starte\w*|start|mach\w*)\b (.{2,60}?)(?: auf| an)?$", t) or \
        re.search(r"^(.{2,40}?) (?:oeffnen|starten|aufmachen)$", _strip_ctx(t))
    if m:
        name = _strip_ctx(m.group(1))
        key = _app_key(name)
        if key:
            return {"op": "open_app", "app": key}
        if DOMAIN.match(name.replace(" ", "")):
            return {"op": "open_site", "site": name.replace(" ", "")}
        return {"op": "unknown_app", "spoken": name}
    return None


def is_confirm(text: str) -> bool | None:
    t = _norm(text)
    if re.match(r"^(ja|jawohl|genau|bestaetigt|mach (?:es|das)|ok(?:ay)?|ja bitte|ja mach)\b", t):
        return True
    if re.match(r"^(nein|nee|abbrechen|stopp?|lass (?:es|das)|doch nicht)\b", t):
        return False
    return None


def execute(osact, cmd: dict) -> dict:
    """Führt einen erkannten PC-Befehl aus. Rückgabe mit spoken (ehrlich), ok, Nachweis."""
    op = cmd.get("op")
    t0 = time.monotonic()
    res: dict = {}
    if op == "blocked":
        return {"ok": False, "op": op, "spoken": "Das mache ich per Sprache aus Sicherheitsgründen nicht. "
                "Dafür brauchst du den NEXUS-Chat mit Freigabe.", "evidence": None}
    if op == "unknown_app":
        return {"ok": False, "op": op, "spoken": f"{cmd.get('spoken') or 'Diese App'} steht nicht auf meiner Liste "
                "für den PC. Ich kann zum Beispiel Chrome, Spotify, Editor, Rechner oder Explorer öffnen.", "evidence": None}
    if op == "open_app":
        res = osact.launch_app(cmd["app"])
        ok = bool(res.get("success"))
        verified = (res.get("verified") or {}).get("running") if isinstance(res.get("verified"), dict) else None
        spoken = (f"{cmd['app'].capitalize()} ist am PC geöffnet." if ok and verified is not False
                  else f"{cmd['app'].capitalize()} wurde gestartet, aber ich sehe den Prozess noch nicht." if ok
                  else f"{cmd['app'].capitalize()} ließ sich am PC nicht öffnen.")
    elif op == "open_site":
        res = osact.open_url("https://" + cmd["site"])
        ok = bool(res.get("success"))
        spoken = f"{cmd['site']} ist am PC im Browser offen." if ok else f"{cmd['site']} ließ sich nicht öffnen."
    elif op == "search":
        res = osact.open_url("https://www.google.com/search?q=" + quote_plus(cmd["query"][:120]))
        ok = bool(res.get("success"))
        spoken = (f"Ich habe am PC nach {cmd['query']} gesucht, die Ergebnisse sind im Browser offen." if ok
                  else "Die Suche ließ sich am PC nicht öffnen.")
    elif op == "volume":
        res = osact.volume(cmd["action"], 4)
        ok = bool(res.get("success"))
        spoken = ({"up": "PC lauter.", "down": "PC leiser.", "mute": "PC stumm geschaltet.",
                   "unmute": "PC-Ton wieder an."}[cmd["action"]] if ok else "Die Lautstärke am PC ließ sich nicht ändern.")
    elif op == "status":
        try:
            import psutil  # type: ignore
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory().percent
            res = {"success": True, "cpu_percent": cpu, "ram_percent": mem}
            ok = True
            spoken = f"Der PC läuft: Prozessor bei {round(cpu)} Prozent, Arbeitsspeicher bei {round(mem)} Prozent."
        except Exception as exc:
            res, ok, spoken = {"success": False, "error": type(exc).__name__}, False, "Den PC-Status kann ich gerade nicht lesen."
    elif op == "close_app":
        if not cmd.get("app"):
            return {"ok": False, "op": op, "spoken": f"{cmd.get('spoken') or 'Diese App'} darf ich am PC nicht schließen.",
                    "evidence": None}
        res = osact.close_app(cmd["app"])
        ok = bool(res.get("success"))
        spoken = f"{cmd['app'].capitalize()} ist am PC geschlossen." if ok else f"{cmd['app'].capitalize()} ließ sich nicht schließen."
    else:
        return {"ok": False, "op": op, "spoken": "Das habe ich nicht verstanden.", "evidence": None}
    evidence = {k: res.get(k) for k in ("success", "action", "app", "target", "method", "verified", "volume_action",
                                        "cpu_percent", "ram_percent", "error") if k in res}
    return {"ok": ok, "op": op, "spoken": spoken, "evidence": evidence, "ms": int((time.monotonic() - t0) * 1000)}
