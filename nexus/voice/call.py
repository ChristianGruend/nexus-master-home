"""KI-Anruf für NEXUSPhone: ein Gesprächszug = Sprache → STT → NEXUS oder Hermes → TTS.

- NEXUS: normale NEXUS-Verarbeitung (ENGINE.chat) in einer eigenen Anrufsitzung CALL-<id>; die aktive
  PC-Chatsitzung wird danach wiederhergestellt, ein Anruf überschreibt keinen anderen Verlauf.
- Hermes: Gesprächsbeitrag des lokalen Hermes-Modells (HermesBrain.converse). Reine Antwort,
  keine Werkzeugausführung, keine erweiterten Rechte durch Sprachbefehle.
- Gespeichert wird nur das Gesprächsprotokoll (Text, Zeiten), nie Audio.
- Kein echtes Telefonnetz: Das ist ein Sprachkanal zwischen NEXUSPhone und dem NEXUS-Core.
"""
from __future__ import annotations

import base64
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from nexus.common.atomic import atomic_write_json, file_lock
from nexus.common.paths import WORKSPACE

CALL_DIR = WORKSPACE / "voice_calls"
CALL_ID = re.compile(r"[A-Za-z0-9_-]{6,40}")
TARGETS = ("nexus", "hermes", "conference")
MAX_AUDIO_BYTES = 2_500_000  # ~75 s PCM 16 kHz mono
MAX_TEXT = 1200
MAX_SPOKEN = 600
HISTORY_TURNS = 8
STT_MODEL = "base"  # genauer als tiny (+~0,3 s), lokal vorhanden
# Nur Namen/Vokabular, keine Beispielsätze: Whisper hat Sätze aus dem Hinweis in unklare Aufnahmen übernommen
# (belegt: "Ich bin der Wetter in Erfurt.", 12.10.2026). Namen bleiben, damit NEXUS/Hermes/Apps erkannt werden.
STT_HINT = "Eddy, NEXUS, Hermes, YouTube, Chrome, Spotify, Rechner, Editor, Explorer, heise.de, Erfurt."
# Modell während eines aktiven Anrufs geladen halten (kalt ~5 s Ladezeit je Zug, warm ~0 s); beim Auflegen entladen.
CALL_KEEP_ALIVE = "10m"

# Stimmen je Gesprächspartner: Hermes dunkel und kräftig (nur Männerstimmen als Ersatz),
# NEXUS bleibt bei der konfigurierten herzlichen Frauenstimme (Amala).
PERSONA_VOICE = {
    "hermes": {"voices": ["de-DE-ConradNeural", "de-DE-KillianNeural", "de-DE-FlorianMultilingualNeural"],
               "pitch": "-12Hz", "rate": "-6%", "volume": "+40%"},
    "nexus": {"voices": ["de-DE-AmalaNeural", "de-DE-KatjaNeural", "de-DE-SeraphinaMultilingualNeural"],
              "volume": "+40%"},
}

HERMES_SYSTEM = ("Du bist Hermes, der interne Arbeitskern von NEXUS, in einem Sprachanruf mit Eddy. Antworte auf "
                 "Deutsch in höchstens drei kurzen, gut sprechbaren Sätzen ohne Aufzählungen oder Markdown. "
                 "Du führst in diesem Gespräch keine Werkzeuge aus und behauptest keine ausgeführten Aktionen; "
                 "Du hast in diesem Gespräch KEINE Live-Daten (kein Wetter, keine Nachrichten, keine Kurse, keine Termine): erfinde so etwas nie, sag ehrlich, dass du es gerade nicht abrufen kannst. "
                 "für echte Aufträge verweist du auf den NEXUS-Chat.")
NEXUS_SYSTEM = ("Du bist NEXUS, die herzliche, aufmerksame Koordinatorin von Eddys KI-System, in einem Sprachanruf. "
                "Antworte auf Deutsch, duze Eddy, in höchstens zwei bis drei kurzen, natürlich gesprochenen Sätzen ohne "
                "Aufzählungen oder Markdown. Du behauptest keine ausgeführten Aktionen. Du hast in diesem Gespräch KEINE Live-Daten (kein Wetter, keine Nachrichten, keine Kurse, keine Termine): erfinde so etwas nie, sag ehrlich, dass du es gerade nicht abrufen kannst. Beginne deine Antwort nie "
                "mit \"Auftrag:\" und kündige keine Ausführung an (kein \"ich werde ... erstellen/einrichten\"). Möchte Eddy "
                "etwas ausgeführt haben, sag ihm kurz, dass er dafür \"Auftrag:\" voranstellen kann.")
WORK_ORDER = re.compile(r"\bauftrag\b", re.I)


SINGLE = ("nexus", "hermes")
REQ_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def voice_of(target: str) -> str:
    """Feste Agentenidentität: im Einzelanruf spricht ausschließlich das gewählte Ziel (auch Wetter/PC-Ansagen);
    nur die Konferenz hat NEXUS als Moderatorin für System-Ansagen. Kein stiller Wechsel zu NEXUS."""
    return target if target in SINGLE else "nexus"


WEATHER_ASK = re.compile(r"(\?|^\W*(?:(?:hey|hallo|ok|okay)\W+)?(?:(?:nexus|hermes)\W+)?(?:wie|wird|was|gibt|regnet|schneit|ist es|"
                         r"sag mir|zeig mir|brauche ich)\b|\bwetter\s+(?:in|für|fuer|im|heute|morgen|am|bei)\b|^\W*wetter\b"
                         # eingebettete Frage ohne Fragezeichen (Whisper setzt oft einen Punkt):
                         # "Ich will wissen, wie in Erfurt das Wetter ist." (echter Zug 12.10.2026 13:54)
                         r"|\b(?:wie|wissen|weisst|weißt|verrat|erzaehl|erzähl)\b[^.!?]*\bwetter\b"
                         r"|\bwetter\b[^.!?]*\b(?:wird|werden)\b)", re.I)


def weather_request(text: str) -> bool:
    """Wetterabfrage nur bei Frage/Abfrageform, nicht bei jeder Aussage mit "Wetter"
    (belegt: "Ich bin ein schönes Wetter." wurde als Abfrage behandelt)."""
    from nexus.voice import live
    return live.is_weather(text) and bool(WEATHER_ASK.search(str(text or "").strip()))


_ACTIVE: dict = {}  # call_id -> letzte Aktivität (warm/turn); entladen nur, wenn kein anderer Anruf aktiv ist


def _touch(call_id: str) -> None:
    now = time.time()
    _ACTIVE[call_id] = now
    for k in [k for k, v in _ACTIVE.items() if now - v > 900]:
        _ACTIVE.pop(k, None)


def unload_model(engine) -> None:
    """Nach Anrufende: Modell gemäß globaler Richtlinie wieder freigeben (keep_alive 0), außer ein anderer
    Anruf ist aktiv (schneller Wechsel NEXUS -> Hermes darf das gerade vorgewärmte Modell nicht entladen)."""
    import threading

    def run():
        if _ACTIVE:
            return
        try:
            llm = engine.master.llm
            model = getattr(llm, "model", None) or llm.pick_model()
            if model:
                llm._req("POST", "/api/generate", {"model": model, "keep_alive": 0}, timeout=15)
        except Exception:
            pass
    threading.Thread(target=run, name="call-unload", daemon=True).start()


def warm(engine, body: dict) -> tuple[int, dict]:
    """Anrufbeginn: Modell im Hintergrund laden (keep_alive nur für die Anrufdauer), damit der erste Zug nicht
    die ~5 s Ladezeit trägt. Kein Modellaufruf mit Inhalt, keine Antwort."""
    call_id = str(body.get("call_id") or "")
    if not CALL_ID.fullmatch(call_id):
        return 400, {"success": False, "error": "BAD_CALL_ID"}
    target = str(body.get("target") or "").lower()
    if target not in TARGETS:
        return 400, {"success": False, "error": "TARGET_NOT_ALLOWED"}
    import threading

    def run():
        try:
            llm = engine.master.llm
            model = getattr(llm, "model", None) or llm.pick_model()
            if model:
                # Laden + eine Mini-Inferenz mit dem Systemprompt des gewählten Agenten: legt dessen Präfix in den
                # Cache und bezahlt den Anlauf der ersten Inferenz, bevor Eddy fertig gesprochen hat.
                system = HERMES_SYSTEM if target == "hermes" else NEXUS_SYSTEM
                llm.chat([{"role": "system", "content": system}, {"role": "user", "content": "Hallo"}],
                         temperature=0.1, num_predict=1, keep_alive=CALL_KEEP_ALIVE)
        except Exception:
            pass
    _touch(call_id)
    threading.Thread(target=run, name="call-warm", daemon=True).start()
    return 200, {"success": True, "call_id": call_id, "target": target, "agent": voice_of(target), "warming": True}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_call_id() -> str:
    return "C" + datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:6]


def _path(call_id: str) -> Path:
    return CALL_DIR / (call_id + ".json")


def load(call_id: str) -> dict | None:
    import json
    try:
        return json.loads(_path(call_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def spoken(text: str, limit: int = MAX_SPOKEN) -> str:
    """Für die Sprachausgabe: Markdown entfernen, an einer Satzgrenze kürzen."""
    t = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)
    t = re.sub(r"[*_#>`|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= limit:
        return t
    cut = max(t.rfind(". ", 0, limit), t.rfind("! ", 0, limit), t.rfind("? ", 0, limit))
    return (t[:cut + 1] if cut > limit // 3 else t[:limit].rsplit(" ", 1)[0] + " …").strip()


def _nexus_talk(engine, history: list[dict], text: str) -> dict:
    """Schneller Gesprächsbeitrag von NEXUS (lokales Modell, kurze Antwort, keine Werkzeuge)."""
    messages = [{"role": "system", "content": NEXUS_SYSTEM}]
    for t in history[-HISTORY_TURNS:]:
        for seg in t.get("segments") or []:
            if seg.get("speaker") == "nexus" and t.get("text") and seg.get("reply"):
                messages += [{"role": "user", "content": t["text"]}, {"role": "assistant", "content": seg["reply"]}]
    messages.append({"role": "user", "content": text})
    try:
        r = engine.master.llm.chat(messages, temperature=0.4, num_predict=160, keep_alive=CALL_KEEP_ALIVE)
    except TypeError:
        r = engine.master.llm.chat(messages)
    return {"success": bool(r.get("success")), "reply": str(r.get("reply") or ""), "model": r.get("model"),
            "error": r.get("error"), "mode": "talk", "timings": r.get("timings")}


def _nexus_reply(engine, call_id: str, text: str, history: list[dict] | None = None) -> dict:
    if history is not None and not WORK_ORDER.search(text):
        return _nexus_talk(engine, history, text)
    sid = "CALL-" + call_id
    lock = getattr(engine, "_save_lock", None)
    if sid not in engine.sessions:
        if lock:
            with lock:
                engine.sessions[sid] = engine._empty_session(sid)
                engine.sessions[sid]["title"] = "NEXUSPhone Anruf " + call_id
        else:
            engine.sessions[sid] = engine._empty_session(sid)
    previous = engine.session_id
    try:
        r = engine.chat(text, sid)
    finally:
        # Aktive PC-Sitzung nicht durch den Anruf umschalten.
        if previous and previous != sid and previous in engine.sessions:
            engine.switch_session(previous)
    return {"success": bool(r.get("success", True)), "reply": str(r.get("reply") or ""), "model": r.get("model"), "mode": "engine",
            "intent": r.get("intent"), "error": r.get("error"), "session_id": sid}


CONFERENCE_NOTE = (" Dies ist eine Konferenzschaltung mit Eddy und NEXUS. Wenn NEXUS gerade geantwortet hat, "
                   "wiederhole das nicht: ergänze in höchstens zwei Sätzen einen eigenen Gedanken, eine Rückfrage "
                   "oder widersprich begründet. Sprich NEXUS dabei ruhig direkt an.")


def _norm(text: str) -> str:
    t = str(text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()


# Handybefehle werden NICHT vom Modell interpretiert: der Server erkennt nur, DASS es ein Befehl ist;
# Auswertung und Ausführung macht die App mit ihrem vorhandenen, registrierten Befehlsparser.
PHONE_CMD = re.compile(
    r"^(?:(?:hey|hallo|ok|okay) )?(?:nexus |hermes )?(?:bitte |kannst du (?:mir )?(?:bitte )?)?"
    r"(?:(?:oeffne|oeffnen|starte|start|open)\b|mach (?:mal )?(?:die |das |den )?\w+ auf\b"
    r"|geh(?:e)? (?:zu|zur|zum|auf|in)|wechsel\w* (?:zu|zur|zum|auf|in)|navigiere"
    r"|(?:die |das |den )?(?:kamera|camera|telefon|einstellungen|termux|wlan|wifi|bluetooth|akku)(?: app| einstellungen)?$"
    r"|status$|statusbericht|systemstatus|lagebericht)")


PHONE_CMD_END = re.compile(r"^(?:(?:hey|hallo|ok) )?(?:nexus |hermes )?(?:kannst du |koenntest du |bitte )*(?:mir )?(?:bitte )?"
                           r"(?:die |das |den )?(?:app )?[a-z0-9][a-z0-9 ]{1,30} (?:oeffnen|starten|aufmachen)(?: bitte)?$")


PHONE_CMD_ANY = re.compile(r"\b(?:oeffne|oeffnen|starte|starten|aufmachen)\b|^(?:auf ne|oeff ne|oeffne mal) \w|\bdie app \w")


def phone_command(text: str) -> bool:
    t = _norm(text)
    # Breite Erkennung ist unkritisch: entscheidet der App-Parser "kein Befehl", wird es ein normaler Gesprächszug.
    return bool(PHONE_CMD.search(t) or PHONE_CMD_END.search(t) or PHONE_CMD_ANY.search(t))


# Wahrheitsschutz: das lokale Modell behauptet trotz Anweisung Wetterwerte oder ausgeführte Aktionen.
FAKE_WEATHER = re.compile(r"\b(sonnig\w*|regnerisch|bewölkt|bedeckt|gewitter|schneit|regnet|\d+\s*grad|wolkenlos|"
                          r"wetter (?:ist|wird) (?:heute )?(?:schön|gut|toll|wunderschön)|wunderschönes wetter)\b", re.I)
FAKE_ACTION = re.compile(r"\b(ich (?:öffne|starte|suche|spiele|schalte|rufe)|(?:habe?|hat|haben) [\w\s]{0,30}?"
                         r"(?:geöffnet|gestartet|gesucht|abgespielt|eingeschaltet|ausgeführt)|(?:öffne|starte) (?:ich|jetzt))\b", re.I)
# Gesprächsmodus ohne Werkzeuge: angekündigte Ausführung ist unbelegt (echt 12.10.2026: "Auftrag: Ich werde einen
# Stadtplan ... erstellen und dir den Link zukommen lassen." / "NEXUS hat die Einstellungen bereits angepasst").
FAKE_PROMISE = re.compile(r"^\W*auftrag\s*:|\b(?:ich|wir) werde\w*\b[^.!?]{0,90}\b(?:erstell\w*|schick\w*|send\w*|zukommen|"
                          r"aktivier\w*|einricht\w*|umsetz\w*|programmier\w*|implementier\w*|erledig\w*|kümmer\w*|"
                          r"sicherstell\w*|änder\w*|verbesser\w*|anpass\w*|install\w*|herunterlad\w*)\b|"
                          r"\b(?:habe?|hat|haben) [\w\s]{0,30}?(?:aktiviert|angepasst|erstellt|eingerichtet|umgesetzt|installiert)\b",
                          re.I)


def guard(reply: str, live: bool) -> tuple[str, str | None]:
    """Ersetzt unbelegte Wetter- oder Ausführungsbehauptungen durch eine ehrliche Aussage."""
    if not live and FAKE_WEATHER.search(reply or ""):
        return ("Aktuelle Wetterdaten habe ich dazu gerade nicht. Sag einfach zum Beispiel: Wetter in Erfurt.",
                "unverified_weather")
    if FAKE_PROMISE.search(reply or ""):
        return ("Im Gespräch kann ich das nicht selbst ausführen. Sag \"Auftrag:\" und was passieren soll, "
                "dann geht es als echter Auftrag mit Nachweis an NEXUS.", "unverified_promise")
    if FAKE_ACTION.search(reply or ""):
        return ("Ausgeführt habe ich dabei nichts. Sag zum Beispiel: Öffne YouTube, dann mache ich es wirklich.",
                "unverified_action")
    return reply, None


def asks(text: str, name: str) -> bool:
    """Richtet sich der Beitrag mit einer Frage an `name` (z. B. Hermes fragt NEXUS)?"""
    low = str(text or "").lower()
    return "?" in low and bool(re.search(r"\b" + name + r"\b", low))


def addressed(text: str) -> list[str]:
    """Konferenz: wer wird angesprochen? "Hermes, ..." -> nur Hermes; "NEXUS, ..." -> nur NEXUS; sonst beide."""
    head = re.sub(r"^\W*(?:hallo|hey|hi|ok|okay|also)\W+", "", text.strip().lower())[:40]
    if re.match(r"(?:hermes|herme[sz])\b", head):
        return ["hermes"]
    if re.match(r"(?:nexus|nexis|näxus)\b", head):
        return ["nexus"]
    return ["nexus", "hermes"]


def _hermes_reply(engine, history: list[dict], text: str, nexus_said: str = "", conference: bool = False) -> dict:
    from nexus.integrations.hermes_brain import HermesBrain
    messages = [{"role": "system", "content": HERMES_SYSTEM + (CONFERENCE_NOTE if conference else "")}]
    for t in history[-HISTORY_TURNS:]:
        for seg in t.get("segments") or ([{"speaker": t.get("target"), "reply": t.get("reply")}] if t.get("reply") else []):
            if seg.get("speaker") == "hermes" and t.get("text") and seg.get("reply"):
                messages += [{"role": "user", "content": t["text"]}, {"role": "assistant", "content": seg["reply"]}]
    messages.append({"role": "user", "content": text if not nexus_said else
                     f"Eddy sagt: {text}\nNEXUS hat gerade geantwortet: {nexus_said[:900]}"})
    try:
        r = HermesBrain(engine.master).converse(messages, keep_alive=CALL_KEEP_ALIVE)
    except TypeError:
        r = HermesBrain(engine.master).converse(messages)
    return {"success": bool(r.get("success")), "reply": str(r.get("reply") or ""), "model": r.get("model"),
            "error": r.get("error"), "provenance": "model_response_unverified", "timings": r.get("timings")}


def _pc_turn(engine, body: dict, call_id: str, target: str, text: str, cmd: dict, stt_ms: int, ids: dict) -> tuple[int, dict]:
    """PC-Befehl ausführen (Positivliste in nexus/voice/pc.py), ehrlich ansagen, mit Nachweis protokollieren."""
    from nexus.voice import pc as pc_mod
    CALL_DIR.mkdir(parents=True, exist_ok=True)
    t1 = time.monotonic()
    with file_lock(_path(call_id)):
        record = load(call_id) or {"call_id": call_id, "started_at": _now(), "turns": [],
                                   "channel": "nexusphone_voice_call", "audio_stored": False}
        record.pop("pending_pc", None)
        if cmd.get("op") == "close_app" and cmd.get("app") and "confirmed" not in cmd:
            record["pending_pc"] = {"cmd": cmd, "ts": time.time()}
            result = {"ok": True, "op": "confirm_request", "evidence": None,
                      "spoken": f"Soll ich {cmd['app'].capitalize()} am PC wirklich schließen? Ungespeichertes geht "
                                f"dabei verloren. Sag ja oder nein."}
        elif cmd.get("confirmed") is False:
            result = {"ok": True, "op": "cancelled", "evidence": None, "spoken": "Gut, ich lasse es offen."}
        else:
            osact = getattr(engine.master, "os_actions", None)
            result = pc_mod.execute(osact, cmd) if osact is not None else \
                {"ok": False, "op": cmd.get("op"), "evidence": None, "spoken": "Die PC-Steuerung ist gerade nicht verfügbar."}
        seg = _segment(engine, voice_of(target), {"success": True, "reply": result["spoken"], "model": "pc_action"},
                       t1, body.get("speak", True))
        executed = result.get("op") not in ("confirm_request", "cancelled", "blocked", "unknown_app")
        if executed or result.get("op") == "blocked":
            record.setdefault("actions", []).append({
                "ts": _now(), "text": text[:300], "kind": "pc_" + str(result.get("op")), "ok": bool(result.get("ok")),
                "target": str(cmd.get("app") or cmd.get("site") or cmd.get("query") or cmd.get("action") or "")[:120],
                "reply": result["spoken"][:300], "executor": "pc_os_actions", "evidence": result.get("evidence")})
        out = {"success": True, "call_id": call_id, "target": target, "text": text, "segments": [seg], "pending": [],
               "speakers": [seg["speaker"]], **ids, "turn_no": len(record["turns"]), "stt_ms": stt_ms,
               "reply_ms": seg["reply_ms"], "reply": seg["reply"], "spoken": seg["spoken"],
               "pc_action": {"op": result.get("op"), "ok": result.get("ok"), "evidence": result.get("evidence")}}
        for k in ("tts_b64", "tts_mime", "tts_bytes", "tts_voice", "tts_error"):
            if k in seg:
                out[k] = seg[k]
        record["turns"].append({"ts": _now(), "target": target, "text": text, "reply": seg["reply"][:2000],
                                "segments": [{k: seg.get(k) for k in ("speaker", "reply", "ok", "reply_ms", "tts_voice")}],
                                "pc_action": out["pc_action"], "success": True, "stt_ms": stt_ms,
                                "reply_ms": seg["reply_ms"], "tts_bytes": seg.get("tts_bytes")})
        record["updated_at"] = _now()
        atomic_write_json(_path(call_id), record)
    return 200, out


def _voice(engine, seg: dict) -> None:
    """Sprachausgabe eines Segments mit der Stimme des Sprechers (ein Neuversuch bei Dienstfehler)."""
    t2 = time.monotonic()
    persona = PERSONA_VOICE.get(seg["speaker"], {})
    tts = engine.master.voice.tts(seg["spoken"], **persona)
    if not (tts.get("success") and tts.get("path")):
        # edge-tts liefert sporadisch HTTP 403: genau ein Neuversuch, danach spricht das Handy lokal.
        seg["tts_retry"] = True
        tts = engine.master.voice.tts(seg["spoken"], **persona)
    if tts.get("success") and tts.get("path"):
        mp3 = Path(tts["path"]).read_bytes()
        seg.update(tts_b64=base64.b64encode(mp3).decode("ascii"), tts_mime="audio/mpeg",
                   tts_bytes=len(mp3), tts_voice=tts.get("voice"))
    else:
        # Keine Dienst-URLs/Tokens an das Handy weitergeben: nur ein kurzer Fehlercode.
        err = re.sub(r"(?:https?|wss?)://\S+", "<url>", str(tts.get("error") or "TTS_FAILED"))
        seg["tts_error"] = err.split(",")[0][:80]
    seg["tts_ms"] = int((time.monotonic() - t2) * 1000)


def turn(engine, body: dict) -> tuple[int, dict]:
    """Ein Anrufzug. Rückgabe (HTTP-Status, JSON)."""
    from nexus.voice import stt as stt_mod
    target = str(body.get("target") or "nexus").strip().lower()
    if target not in TARGETS:
        return 400, {"success": False, "error": "TARGET_NOT_ALLOWED", "allowed": list(TARGETS)}
    call_id = str(body.get("call_id") or "").strip() or new_call_id()
    if not CALL_ID.fullmatch(call_id):
        return 400, {"success": False, "error": "BAD_CALL_ID"}
    # Zuordnung: jede Antwort trägt call_id, target, turn_id und request_id des Zugs; das Handy verwirft Abweichungen.
    turn_id = str(body.get("turn_id") or "").strip()
    request_id = str(body.get("request_id") or "").strip() or uuid.uuid4().hex[:16]
    if (turn_id and not REQ_ID.fullmatch(turn_id)) or not REQ_ID.fullmatch(request_id):
        return 400, {"success": False, "error": "BAD_TURN_OR_REQUEST_ID"}
    ids = {"turn_id": turn_id or None, "request_id": request_id, "agent": voice_of(target),
           "session_id": "CALL-" + call_id + "-" + target}
    prior = load(call_id)
    if call_id not in _ENDED:
        _touch(call_id)
    if call_id in _ENDED or (prior and prior.get("ended_at")):
        return 409, {"success": False, "call_id": call_id, "target": target, "error": "CALL_ENDED", **ids}
    if prior and prior.get("target") and prior["target"] != target:
        # Ein Anruf hat genau ein Ziel; ein Zielwechsel innerhalb derselben Anruf-ID ist ein Zuordnungsfehler.
        return 409, {"success": False, "call_id": call_id, "target": target, "error": "TARGET_MISMATCH",
                     "call_target": prior["target"], **ids}
    b64 = body.get("audio_b64") or ""
    if isinstance(b64, str) and "," in b64:
        b64 = b64.split(",", 1)[1]
    raw = b""
    if b64:
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception:
            return 400, {"success": False, "error": "BAD_AUDIO_B64"}
        if len(raw) > MAX_AUDIO_BYTES:
            return 413, {"success": False, "error": "AUDIO_TOO_LARGE"}
    t0 = time.monotonic()
    text, stt_info = str(body.get("text") or "").strip()[:MAX_TEXT], None
    if raw:
        stt_info = stt_mod.transcribe_bytes(raw, filename=str(body.get("filename") or "call.wav"),
                                            language=str(body.get("language") or "de"),
                                            model_size=str(body.get("stt_model") or STT_MODEL),
                                            initial_prompt=STT_HINT)
        if not stt_info.get("success"):
            return 200, {"success": False, "call_id": call_id, "target": target,
                         "error": stt_info.get("error") or "STT_FAILED", **ids}
        text = str(stt_info.get("text") or "").strip()[:MAX_TEXT]
    stt_ms = int((time.monotonic() - t0) * 1000)
    if not text:
        return 200, {"success": False, "call_id": call_id, "target": target, "error": "EMPTY_TRANSCRIPT",
                     "stt_ms": stt_ms, **ids}

    # PC-Befehle zuerst ("Öffne Chrome am PC" ist kein Handybefehl); offene Rückfrage (Schließen) beantworten.
    from nexus.voice import pc as pc_mod
    pc_cmd = None if body.get("force_talk") else pc_mod.parse(text)
    pending = (load(call_id) or {}).get("pending_pc") if CALL_ID.fullmatch(call_id) else None
    if pending and time.time() - pending.get("ts", 0) < pc_mod.CLOSE_TTL_S:
        answer = pc_mod.is_confirm(text)
        if answer is not None:
            pc_cmd = dict(pending["cmd"], confirmed=answer)
    if pc_cmd:
        return _pc_turn(engine, body, call_id, target, text, pc_cmd, stt_ms, ids)

    if not body.get("force_talk") and phone_command(text):
        # Befehl für das Handy: kein Modellaufruf, die App wertet ihn mit ihrem Parser aus und meldet das
        # Ergebnis über /api/voice/call/action (Beleg im Gesprächsprotokoll).
        return 200, {"success": True, "call_id": call_id, "target": target, "text": text, "phone_command": text,
                     "segments": [], "pending": [], "stt_ms": stt_ms, "reply_ms": 0, **ids}

    CALL_DIR.mkdir(parents=True, exist_ok=True)
    with file_lock(_path(call_id)):
        record = load(call_id) or {"call_id": call_id, "started_at": _now(), "turns": [],
                                   "channel": "nexusphone_voice_call", "audio_stored": False}
        record.setdefault("target", target)
        speakers = addressed(text) if target == "conference" else [target]
        segments, nexus_said, deferred = [], "", []
        live_data = None
        from nexus.voice import live
        awaiting = record.get("await_place") and len(text.split()) <= 5 and not WORK_ORDER.search(text)
        if awaiting or (weather_request(text) and not WORK_ORDER.search(text)):
            # Echte Wetterdaten statt Modellwissen; NEXUS spricht das Ergebnis mit Quelle im Protokoll.
            t1 = time.monotonic()
            live_data = live.weather(("Wetter in " + text) if awaiting and not live.is_weather(text) else text)
            record["await_place"] = bool(live_data.get("needs") == "place" or live_data.get("found") is False)
            seg = _segment(engine, voice_of(target), {"success": bool(live_data.get("spoken")), "reply": live_data.get("spoken"),
                                             "model": "open-meteo", "error": live_data.get("error")},
                           t1, body.get("speak", True), live=True)
            seg["source"] = live_data.get("source")
            segments.append(seg)
            speakers = []
        if target == "conference" and speakers == ["nexus", "hermes"]:
            # Hermes läuft parallel zur Sprachausgabe von NEXUS; das Handy holt ihn über /next.
            deferred, speakers = ["hermes"], ["nexus"]
        if not live_data:
            record.pop("await_place", None)
        for who in speakers:
            t1 = time.monotonic()
            if who == "nexus":
                reply = _nexus_reply(engine, call_id, text, record["turns"])
                checked = reply.get("reply") or ""
                if reply.get("mode") != "engine":
                    checked = guard(checked, False)[0]
                nexus_said = spoken(checked, 900)  # Hermes bekommt nur die geprüfte Fassung
                if deferred:
                    turn_no = len(record["turns"])
                    _start_hermes(engine, call_id, turn_no, list(record["turns"]), text, nexus_said, body.get("speak", True))
            else:
                reply = _hermes_reply(engine, record["turns"], text, nexus_said, conference=target == "conference")
            seg = _segment(engine, who, reply, t1, body.get("speak", True))
            if who == "nexus":
                nexus_said = seg["spoken"]  # Hermes bezieht sich auf die geprüfte Fassung
            segments.append(seg)
        allowed = SINGLE if target == "conference" else (target,)
        bad = [x.get("speaker") for x in segments if x.get("speaker") not in allowed]
        if bad:
            # darf nach voice_of() nicht vorkommen; harte Sperre statt Ausgabe mit falscher Identität
            return 500, {"success": False, "call_id": call_id, "target": target, "error": "AGENT_IDENTITY_VIOLATION",
                         "speakers": bad, **ids}
        first = segments[0] if segments else {"reply": "", "spoken": ""}
        reply_ms = sum(x["reply_ms"] for x in segments)
        out = {"success": any(x["ok"] for x in segments), "call_id": call_id, "target": target, "text": text,
               "speakers": speakers + deferred, "segments": segments, "pending": deferred,
               "turn_no": len(record["turns"]), **ids,
               "live": {k: live_data.get(k) for k in ("source", "place", "found", "data")} if live_data else None,
               # Einzelgespräch: Felder des einzigen Segments auch oben (ältere App-Versionen).
               "reply": first["reply"], "spoken": first["spoken"], "model": first.get("model"),
               "error": None if any(x["ok"] for x in segments) else (first.get("error") or "EMPTY_REPLY"),
               "stt_ms": stt_ms, "reply_ms": reply_ms,
               "stt": {k: (stt_info or {}).get(k) for k in ("language", "duration", "model")} if stt_info else None}
        for k in ("tts_b64", "tts_mime", "tts_bytes", "tts_voice", "tts_error", "tts_retry", "tts_ms"):
            if k in first:
                out[k] = first[k]
        if call_id in _ENDED or (load(call_id) or {}).get("ended_at"):
            # Während der Modellantwort aufgelegt: nicht mehr ausliefern (das Handy würde sie ohnehin verwerfen).
            return 409, {"success": False, "call_id": call_id, "target": target, "error": "CALL_ENDED", **ids}
        record["turns"].append({"ts": _now(), "target": target, "text": text, "reply": first["reply"][:2000],
                                "turn_id": ids["turn_id"], "request_id": request_id,
                                "segments": [{k: x.get(k) for k in ("speaker", "reply", "ok", "error", "reply_ms",
                                                                    "tts_voice", "tts_bytes", "source", "guarded",
                                                                    "original", "llm", "tts_ms")} for x in segments],
                                "live": out.get("live"),
                                "success": out["success"], "error": out["error"], "stt_ms": stt_ms,
                                "reply_ms": reply_ms, "tts_bytes": first.get("tts_bytes")})
        record["updated_at"] = _now()
        atomic_write_json(_path(call_id), record)
    return 200, out


_PENDING: dict = {}
# Sofort gesetzt beim Auflegen (ohne Datei-Sperre, die ein laufender Zug hält): laufende Züge liefern nicht mehr aus.
_ENDED: set = set()
_PENDING_LOCK = __import__("threading").Lock()


def _segment(engine, who: str, reply: dict, t1: float, speak: bool, live: bool = False) -> dict:
    text, guarded = str(reply.get("reply") or ""), None
    if reply.get("model") not in ("open-meteo", "pc_action") and reply.get("mode") != "engine":
        text, guarded = guard(text, live)
    seg = {"speaker": who, "reply": text, "model": reply.get("model"),
           "error": reply.get("error"), "reply_ms": int((time.monotonic() - t1) * 1000)}
    if reply.get("timings"):
        seg["llm"] = reply["timings"]
    if guarded:
        seg.update(guarded=guarded, original=str(reply.get("reply") or "")[:600])
    seg["spoken"] = spoken(seg["reply"])
    seg["ok"] = bool(reply.get("success")) and bool(seg["spoken"])
    if seg["spoken"] and speak:
        _voice(engine, seg)
    return seg


def _persist_segment(call_id: str, turn_no: int, seg: dict) -> None:
    with file_lock(_path(call_id)):
        record = load(call_id)
        if record and turn_no < len(record["turns"]):
            record["turns"][turn_no].setdefault("segments", []).append(
                {k: seg.get(k) for k in ("speaker", "reply", "ok", "error", "reply_ms", "tts_voice", "tts_bytes",
                                         "guarded", "original")})
            atomic_write_json(_path(call_id), record)


def _register(call_id: str, turn_no: int, step: int):
    import threading
    ev, box = threading.Event(), {}
    with _PENDING_LOCK:
        _PENDING[(call_id, turn_no, step)] = (ev, box, time.monotonic())
        for k in [k for k, v in _PENDING.items() if time.monotonic() - v[2] > 600]:
            _PENDING.pop(k, None)
    return ev, box


def _start_hermes(engine, call_id, turn_no, history, text, nexus_said, speak):
    """Schritt 1: Hermes ergänzt NEXUS. Fragt Hermes NEXUS direkt, folgt Schritt 2: NEXUS antwortet
    (höchstens ein KI-zu-KI-Wechsel pro Gesprächszug, danach ist wieder Eddy dran)."""
    import threading
    ev1, box1 = _register(call_id, turn_no, 1)

    def run():
        t1 = time.monotonic()
        try:
            reply = _hermes_reply(engine, history, text, nexus_said, conference=True)
        except Exception as exc:
            reply = {"success": False, "reply": "", "error": type(exc).__name__}
        seg = _segment(engine, "hermes", reply, t1, speak)
        _persist_segment(call_id, turn_no, seg)
        follow = seg["ok"] and asks(seg["reply"], "nexus")
        ev2 = box2 = None
        if follow:
            ev2, box2 = _register(call_id, turn_no, 2)
        box1.update(seg=seg, more=follow)
        ev1.set()  # Hermes sofort ausliefern; die Antwort von NEXUS entsteht parallel zur Wiedergabe
        if follow:
            t2 = time.monotonic()
            try:
                answer = _nexus_talk(engine, history, f"Eddy hatte gesagt: {text}\nHermes fragt dich jetzt direkt: "
                                                      f"{seg['spoken'][:600]}\nAntworte Hermes kurz und sprich ihn an.")
            except Exception as exc:
                answer = {"success": False, "reply": "", "error": type(exc).__name__}
            seg2 = _segment(engine, "nexus", answer, t2, speak)
            seg2["reply_to"] = "hermes"
            _persist_segment(call_id, turn_no, seg2)
            box2.update(seg=seg2, more=False)
            ev2.set()

    threading.Thread(target=run, name="call-hermes", daemon=True).start()


def next_segment(body: dict) -> tuple[int, dict]:
    """Nachgereichter Konferenzbeitrag: step 1 = Hermes, step 2 = Antwort von NEXUS auf Hermes."""
    call_id = str(body.get("call_id") or "")
    if not CALL_ID.fullmatch(call_id):
        return 400, {"success": False, "error": "BAD_CALL_ID"}
    try:
        turn_no, step = int(body.get("turn_no")), int(body.get("step") or 1)
    except (TypeError, ValueError):
        return 400, {"success": False, "error": "BAD_TURN_NO"}
    with _PENDING_LOCK:
        item = _PENDING.get((call_id, turn_no, step))
    if not item:
        return 404, {"success": False, "error": "NOTHING_PENDING"}
    ev, box, _ = item
    if not ev.wait(timeout=float(body.get("wait_s") or 90)):
        return 200, {"success": False, "error": "STILL_RUNNING", "call_id": call_id, "turn_no": turn_no, "step": step}
    with _PENDING_LOCK:
        _PENDING.pop((call_id, turn_no, step), None)
    seg = box["seg"]
    return 200, {"success": seg["ok"], "call_id": call_id, "turn_no": turn_no, "step": step,
                 "segments": [seg], "more": bool(box.get("more"))}


def action(body: dict) -> tuple[int, dict]:
    """Beleg einer auf dem Handy ausgeführten Aktion (vom registrierten App-Parser ausgewertet)."""
    call_id = str(body.get("call_id") or "")
    if not CALL_ID.fullmatch(call_id):
        return 400, {"success": False, "error": "BAD_CALL_ID"}
    entry = {"ts": _now(), "text": str(body.get("text") or "")[:300], "kind": str(body.get("kind") or "")[:20],
             "target": str(body.get("detail") or "")[:120], "ok": bool(body.get("ok")),
             "reply": str(body.get("reply") or "")[:300], "executor": "nexusphone_app_parser"}
    with file_lock(_path(call_id)):
        record = load(call_id) or {"call_id": call_id, "started_at": _now(), "turns": [],
                                   "channel": "nexusphone_voice_call", "audio_stored": False}
        record.setdefault("actions", []).append(entry)
        atomic_write_json(_path(call_id), record)
    return 200, {"success": True, "call_id": call_id, "actions": len(record["actions"])}


def end(body: dict, engine=None) -> tuple[int, dict]:
    call_id = str(body.get("call_id") or "")
    if not CALL_ID.fullmatch(call_id):
        return 400, {"success": False, "error": "BAD_CALL_ID"}
    _ENDED.add(call_id)
    _ACTIVE.pop(call_id, None)
    if len(_ENDED) > 5000:
        _ENDED.clear(); _ENDED.add(call_id)
    with _PENDING_LOCK:
        for k in [k for k in _PENDING if k[0] == call_id]:
            _PENDING.pop(k, None)
    if engine is not None:
        unload_model(engine)
    try:
        turns = _write_end(call_id, body, timeout=1.5)
    except TimeoutError:
        # Ein Zug hält gerade die Sperre (Modellantwort läuft): Auflegen sofort bestätigen, ended_at nachtragen.
        # Der laufende Zug liefert wegen _ENDED nicht mehr aus.
        import threading
        threading.Thread(target=_write_end_retry, args=(call_id, dict(body)), name="call-end", daemon=True).start()
        return 200, {"success": True, "call_id": call_id, "deferred": True}
    return 200, {"success": True, "call_id": call_id, "turns": turns}


def _write_end(call_id: str, body: dict, timeout: float) -> int:
    with file_lock(_path(call_id), timeout=timeout):
        record = load(call_id)
        if record is None:
            # Aufgelegt, bevor der erste Zug gespeichert war: Grabstein anlegen, damit ein noch laufender
            # Zug dieses Anrufs auch nach einem Neustart nicht mehr ausgeliefert wird (turn() prüft ended_at).
            CALL_DIR.mkdir(parents=True, exist_ok=True)
            record = {"call_id": call_id, "started_at": _now(), "turns": [], "channel": "nexusphone_voice_call",
                      "audio_stored": False, "tombstone": True}
        record["ended_at"] = _now()
        record["end_reason"] = str(body.get("reason") or "hangup")[:40]
        record["client_stats"] = {k: body.get(k) for k in ("duration_s", "turns", "muted_s", "route")
                                  if isinstance(body.get(k), (int, float, str))}
        atomic_write_json(_path(call_id), record)
        return len(record["turns"])


def _write_end_retry(call_id: str, body: dict) -> None:
    for _ in range(12):
        try:
            _write_end(call_id, body, timeout=30)
            return
        except TimeoutError:
            continue
