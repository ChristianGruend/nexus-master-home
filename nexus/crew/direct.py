"""Direkte Crew-Lieferung bei klaren Auftraegen (statt Rueckfrage).

- @Coder/@Scout + YouTube-Link: echte Live-Websuche (Research), nur echte YouTube-URLs.
- @Fixer + "behebe den fehler": letzte echte Fehler aus Server-/Status-Logs als Kontext.
Nichts wird erfunden: ohne Treffer faellt der Lauf auf den normalen Crew-Ablauf zurueck.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nexus.common.paths import WORKSPACE
from nexus.crew import fixer_actions as _fx

YT_URL = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|playlist\?list=|shorts/)|youtu\.be/)", re.I)
FILLER = set("""zeig zeige mir mal bitte ein einen eine link links für fuer fur von zu zum zur mit dessen deren
seinem ihrem thema themen youtube video videos gib such suche finde den die das der auf und oder über ueber""".split())
DEFAULT_TOPIC = {"coder": "Programmieren lernen Tutorial deutsch", "scout": "aktuelle Technik News deutsch"}
FIX_RE = re.compile(r"\b(?:behebe?|beheb\w*|fix\w*|repariere?\w*)\b.*\bfehler\b|\bfehler\b.*\b(?:beheb\w*|fix\w*)", re.I)
ERR_RE = re.compile(r"Traceback|Error|Exception|CRITICAL|FAIL|\" 5\d\d |WARN", re.I)
IGNORE_RE = re.compile(r"/api/bridge/health HTTP/1\.1\" 401")


def _tag(agent: dict) -> str:
    return str(agent.get("tag") or agent.get("id") or "").strip().lower()


def youtube_topic(message: str, tag: str) -> str:
    text = re.sub(r"@\w+", " ", message or "").split("[Antwortvorgabe", 1)[0]
    words = [w for w in re.findall(r"[\wäöüÄÖÜß-]{3,}", text.lower()) if w not in FILLER]
    return " ".join(words[:8]) if words else DEFAULT_TOPIC.get(tag, "Tutorial deutsch")


def youtube_links(research: Any, topic: str, limit: int = 3) -> tuple[list[dict], dict]:
    res: dict = {"success": False}
    hits: list[dict] = []
    if research is None:
        return hits, res
    for q in ("youtube " + topic, topic + " site:youtube.com"):
        res = research.search(q, 10) or {}
        hits = [r for r in (res.get("results") or []) if YT_URL.match(str(r.get("url") or ""))]
        if hits:
            break
    if not hits:
        yt = youtube_page_search(topic, limit)
        if yt:
            return yt, {"success": True, "source": "YouTube-Suche", "results": yt}
    return hits[:limit], res


YT_RENDERER = re.compile(r'"videoRenderer":\{"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"\}', re.S)


def parse_youtube_results(html: str, limit: int = 3) -> list[dict]:
    out, seen = [], set()
    for m in YT_RENDERER.finditer(html or ""):
        vid, title = m.group(1), m.group(2)
        if vid in seen:
            continue
        seen.add(vid)
        try:
            title = json.loads('"' + title + '"')
        except ValueError:
            pass
        out.append({"title": title, "url": "https://www.youtube.com/watch?v=" + vid, "snippet": ""})
        if len(out) >= limit:
            break
    return out


def youtube_page_search(topic: str, limit: int = 3) -> list[dict]:
    """Fallback: oeffentliche YouTube-Ergebnisseite (kostenlos, ohne Konto/Token)."""
    import urllib.parse
    import urllib.request
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(topic) + "&hl=de"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "de-DE"})
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
    except Exception:
        return []
    return parse_youtube_results(html, limit)


def last_errors(root: Path | None = None, limit: int = 8) -> list[str]:
    logs = (root or WORKSPACE) / "logs"
    found: list[str] = []
    for name in ("nexus_server.err.log", "nexus_os_status.log", "nexus_control.log"):
        p = logs / name
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
        except OSError:
            continue
        for line in lines:
            if ERR_RE.search(line) and not IGNORE_RE.search(line) and not _fx.is_acked(line, root):
                found.append(name + ": " + line.strip()[:300])
    return found[-limit:]


LIVE_RE = re.compile(r"\b(?:heute|neu\w*|aktuell\w*|news|nachrichten|recherch\w*|such\w* (?:im )?(?:netz|web|internet)|was gibt es)\b", re.I)
LIVE_FILLER = set("was ist sind heute neu neues neue neuen neuigkeiten dazu gekommen dazugekommen gibt es aktuell aktuelle aktuellen news nachrichten recherchiere recherche such suche netz netzt web internet mal bitte zum zur zu dem den der die das im in und mir sag erzaehl".split())


def live_query(message: str) -> str:
    text = re.sub(r"@\w+", " ", message or "").split("[Antwortvorgabe", 1)[0]
    words = [w for w in re.findall(r"[^\W_]{3,}", text.lower()) if w not in LIVE_FILLER]
    return " ".join(words[:8])


def live_research(message: str, master: Any) -> dict | None:
    from datetime import date
    q = live_query(message)
    if not q:
        return None
    research = getattr(master, "research", None)
    if research is None:
        from nexus.knowledge.research import Research
        research = Research()
    low = (message or "").lower()
    news = bool(re.search(r"\b(?:heute|neu\w*|aktuell\w*|news|nachrichten)\b", low))
    try:
        res = research.search(q, 6, news=news) or {}
    except TypeError:
        res = research.search(q, 6) or {}
    hits = res.get("results") or []
    today = date.today().strftime("%d.%m.%Y")
    if not hits:
        return {"final": f"Live-Websuche (Stand {today}) zu \"{q}\": keine passenden Treffer "
                         f"(geprueft: {', '.join(res.get('tried') or [])}). Nichts erfunden.",
                "tools": [{"name": "web_search", "args": {"query": q, "news": news},
                           "result": {"success": False, "results": [], "tried": res.get("tried")}}]}
    lines = [f"Live-Websuche ({res.get('source') or 'Web'}), Stand {today}, Thema: {q}", ""]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h.get('title')}")
        if h.get("snippet"):
            lines.append("   " + str(h["snippet"]).strip()[:240])
        lines.append("   " + str(h.get("url")))
    lines += ["", "Quellen: echte Live-Treffer oben (nur Titel/Snippet/URL, nichts erfunden)."]
    return {"final": "\n".join(lines),
            "tools": [{"name": "web_search", "args": {"query": q, "news": news},
                       "result": {"success": True, "results": hits, "source": res.get("source")}}]}


def prepare(agent: dict, message: str, master: Any) -> dict | None:
    tag = _tag(agent)
    low = (message or "").lower()
    if tag in ("coder", "scout") and "youtube" in low and any(w in low for w in ("link", "video")):
        topic = youtube_topic(message, tag)
        hits, res = youtube_links(getattr(master, "research", None), topic)
        if not hits:
            return None
        lines = [f"@{agent.get('tag')} hat live gesucht ({res.get('source') or 'Websuche'}), Thema: {topic}", ""]
        for i, h in enumerate(hits, 1):
            title = re.sub(r"\s*-\s*YouTube\s*$", "", str(h.get("title") or "")).strip()
            lines.append(f"{i}. {title}")
            if h.get("snippet"):
                lines.append("   " + str(h["snippet"]).strip()[:200])
            lines.append("   " + str(h["url"]))
        lines += ["", "Quelle: Live-Websuche, nur echte YouTube-Links."]
        trace = [{"name": "web_search", "args": {"query": "youtube " + topic},
                  "result": {"success": True, "results": hits, "source": res.get("source")}}]
        return {"final": "\n".join(lines), "tools": trace}
    if tag != "fixer" and LIVE_RE.search(low):
        out = live_research(message, master)
        if out:
            return out
    if tag == "fixer" and FIX_RE.search(low):
        errs = last_errors()
        acts = _fx.plan(errs)
        if not errs and not acts:
            return {"final": "@Fixer hat die Logs geprueft (nexus_server.err.log, nexus_os_status.log, "
                             "nexus_control.log) und System geprueft: kein aktueller Fehler. Nichts zu beheben. PASS",
                    "tools": [{"name": "read_logs", "args": {}, "result": {"success": True, "errors": []}}]}
        results = _fx.run(errs, actions=acts)
        left = last_errors()
        lines = ["Diagnose + Selbst-Fix (Whitelist, nur G:, headless).", "", "Gefunden:"]
        lines += ["- " + e for e in errs[-5:]] or ["- (keine Logfehler)"]
        lines += ["", "Ausgefuehrt + nachgeprueft:"]
        lines += [f"- {r['action']}: {r['result']} - {r['detail']}" for r in results] or ["- keine sichere Aktion passend"]
        ok = all(r["result"] == "PASS" for r in results) and not left
        if left:
            lines += ["", "Noch offen (kein sicherer Auto-Fix; Code-Patch nur ueber Selbstbau-Weg mit Backup/Tests/Rollback):"]
            lines += ["- " + e for e in left[-5:]]
        lines += ["", "Gesamt: " + ("PASS - Fehler weg." if ok else "FAIL - nicht alles behoben (siehe oben).")]
        return {"final": "\n".join(lines), "errors": errs,
                "tools": [{"name": "fixer_auto", "args": {"actions": acts},
                           "result": {"success": ok, "results": results, "remaining": left}}]}
    return None

META_LINE = re.compile(r"^\s*(?:\(\d+\)\s*)?(?:\?\s*)?(?:Gerade erledigt|Als N\S*chstes|Ich (?:suche|schaue|pr\S*fe|starte|werde)|Suche gestartet|Gelesen \(Logs\)|@\w+ (?:\S*bernimmt|liefert direkt)|Ergebnis:\s*$|Status:|N\S*chster Schritt:)", re.I)
TOOL_JSON = re.compile(r"\{\s*\"name\"\s*:\s*\"[\w.-]+\"\s*,\s*\"(?:arguments|parameters|args)\"[\s\S]*?\}\s*\}?", re.S)


def result_only(text: str) -> str:
    """Nur das eigentliche Ergebnis: Meta-/Fortschrittszeilen und Text-Tool-JSON entfernen."""
    t = TOOL_JSON.sub("", str(text or ""))
    t = re.sub(r"<tool_call>[\s\S]*?(?:</tool_call>|$)", "", t)
    lines = [l for l in t.splitlines() if not META_LINE.match(l)]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out or str(text or "").strip()
