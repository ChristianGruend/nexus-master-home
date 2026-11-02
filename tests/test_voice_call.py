"""NEXUSPhone KI-Anruf: Backend-Gesprächszug (nexus/voice/call.py)."""
import base64
import json
from types import SimpleNamespace

import pytest

from nexus.voice import call


class FakeEngine:
    def __init__(self):
        self.sessions = {"CHAT-A": {"id": "CHAT-A", "messages": []}}
        self.session_id = "CHAT-A"
        self.calls = []
        self.talk = []
        outer = self

        class LLM:
            def chat(self, messages, **kw):
                outer.talk.append(messages)
                return {"success": True, "reply": "Hallo Eddy, schön dich zu hören.", "model": "qwen"}

        self.master = SimpleNamespace(voice=SimpleNamespace(tts=lambda t, **kw: {"success": False, "error": "offline"}),
                                      llm=LLM())

    def _empty_session(self, sid):
        return {"id": sid, "messages": []}

    def chat(self, text, sid):
        self.calls.append((text, sid))
        self.session_id = sid  # wie die echte Engine: aktive Sitzung wird umgeschaltet
        return {"success": True, "reply": "**Hallo** Eddy. " + "Satz. " * 200, "model": "m"}

    def switch_session(self, sid):
        self.session_id = sid
        return {"success": True}


@pytest.fixture(autouse=True)
def call_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(call, "CALL_DIR", tmp_path)
    return tmp_path


def test_rejects_unknown_target_and_bad_ids():
    e = FakeEngine()
    assert call.turn(e, {"target": "shell", "text": "x"})[1]["error"] == "TARGET_NOT_ALLOWED"
    assert call.turn(e, {"call_id": "../../x", "text": "x"})[1]["error"] == "BAD_CALL_ID"
    assert call.turn(e, {"audio_b64": "%%%"})[1]["error"] == "BAD_AUDIO_B64"
    assert not e.calls


def test_empty_transcript_makes_no_model_call(monkeypatch):
    from nexus.voice import stt
    monkeypatch.setattr(stt, "transcribe_bytes", lambda *a, **k: {"success": True, "text": "  "})
    e = FakeEngine()
    code, out = call.turn(e, {"audio_b64": base64.b64encode(b"RIFF....").decode()})
    assert out["error"] == "EMPTY_TRANSCRIPT" and not e.calls


def test_nexus_turn_uses_own_session_and_restores_active(call_dir):
    e = FakeEngine()
    code, out = call.turn(e, {"call_id": "C123456", "text": "Auftrag: Wie spät ist es?"})
    assert code == 200 and out["success"]
    assert e.calls == [("Auftrag: Wie spät ist es?", "CALL-C123456")]
    assert e.session_id == "CHAT-A"
    assert "**" not in out["spoken"] and len(out["spoken"]) <= call.MAX_SPOKEN + 2
    record = json.loads((call_dir / "C123456.json").read_text(encoding="utf-8"))
    assert record["audio_stored"] is False and record["turns"][0]["text"] == "Auftrag: Wie spät ist es?"
    assert out["tts_error"] == "offline"


def test_hermes_turn_is_conversation_only(monkeypatch):
    seen = {}

    class Brain:
        def __init__(self, master):
            pass

        def converse(self, messages):
            seen["messages"] = messages
            return {"success": True, "reply": "Hier ist Hermes.", "model": "qwen"}

    import nexus.integrations.hermes_brain as hb
    monkeypatch.setattr(hb, "HermesBrain", Brain)
    e = FakeEngine()
    call.turn(e, {"call_id": "C777777", "target": "hermes", "text": "Hallo Hermes"})
    code, out = call.turn(e, {"call_id": "C777777", "target": "hermes", "text": "Und jetzt?"})
    assert out["reply"] == "Hier ist Hermes." and not e.calls
    roles = [m["role"] for m in seen["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert "keine Werkzeuge" in seen["messages"][0]["content"]


def test_end_records_stats(call_dir):
    e = FakeEngine()
    call.turn(e, {"call_id": "C999999", "text": "hi"})
    code, out = call.end({"call_id": "C999999", "reason": "hangup", "duration_s": 12, "route": "speaker"})
    assert code == 200 and out["turns"] == 1
    # Design: Auflegen ohne vorherigen Zug legt einen Grabstein an (call.py _write_end) -> 200, turns 0
    code2, out2 = call.end({"call_id": "C000000"})
    assert code2 == 200 and out2["turns"] == 0


def test_tts_retry_once_and_error_without_urls():
    e = FakeEngine()
    calls = []

    def tts(text, **kw):
        calls.append(text)
        return {"success": False, "error": "403, message='Invalid', url='wss://speech.example/x?TrustedClientToken=abc'"}

    e.master.voice.tts = tts
    code, out = call.turn(e, {"call_id": "C555555", "text": "Hallo"})
    assert len(calls) == 2 and out["tts_retry"] is True
    assert "Token" not in out["tts_error"] and "wss://" not in out["tts_error"]


def test_conference_nexus_then_hermes_with_context(monkeypatch):
    seen = {}

    class Brain:
        def __init__(self, master):
            pass

        def converse(self, messages):
            seen["messages"] = messages
            return {"success": True, "reply": "Ich ergänze: lass uns das testen.", "model": "qwen"}

    import nexus.integrations.hermes_brain as hb
    monkeypatch.setattr(hb, "HermesBrain", Brain)
    e = FakeEngine()
    code, out = call.turn(e, {"call_id": "C111111", "target": "conference", "text": "Was ist euer Plan?"})
    assert out["speakers"] == ["nexus", "hermes"] and out["pending"] == ["hermes"]
    assert [s["speaker"] for s in out["segments"]] == ["nexus"] and not e.calls  # schneller Gesprächsweg
    code, nxt = call.next_segment({"call_id": "C111111", "turn_no": out["turn_no"], "wait_s": 5})
    assert nxt["segments"][0]["speaker"] == "hermes" and nxt["success"]
    assert "NEXUS hat gerade geantwortet" in seen["messages"][-1]["content"]
    assert "Konferenzschaltung" in seen["messages"][0]["content"]
    record = call.load("C111111")
    assert [x["speaker"] for x in record["turns"][0]["segments"]] == ["nexus", "hermes"]
    code, out = call.turn(e, {"call_id": "C111111", "target": "conference", "text": "Hermes, und du?"})
    assert out["speakers"] == ["hermes"] and out["pending"] == []
    assert call.next_segment({"call_id": "C111111", "turn_no": 99})[0] == 404


def test_persona_voices_hermes_male_only():
    assert all("Conrad" in v or "Killian" in v or "Florian" in v for v in call.PERSONA_VOICE["hermes"]["voices"])
    assert call.PERSONA_VOICE["hermes"]["pitch"].startswith("-")



def test_nexus_talk_is_fast_path_and_work_order_uses_engine():
    e = FakeEngine()
    code, out = call.turn(e, {"call_id": "C222222", "text": "Wie geht es dir?"})
    assert out["success"] and not e.calls and "herzliche" in e.talk[0][0]["content"]
    call.turn(e, {"call_id": "C222222", "text": "Auftrag: erstelle eine Notiz"})
    assert len(e.calls) == 1



def test_hermes_question_to_nexus_gets_answer_once(monkeypatch):
    class Brain:
        def __init__(self, master):
            pass

        def converse(self, messages):
            return {"success": True, "reply": "Klingt gut. NEXUS, was meinst du dazu?", "model": "qwen"}

    import nexus.integrations.hermes_brain as hb
    monkeypatch.setattr(hb, "HermesBrain", Brain)
    e = FakeEngine()
    code, out = call.turn(e, {"call_id": "C333333", "target": "conference", "text": "Was machen wir heute?"})
    code, h = call.next_segment({"call_id": "C333333", "turn_no": out["turn_no"], "wait_s": 5})
    assert h["segments"][0]["speaker"] == "hermes" and h["more"] is True
    code, n = call.next_segment({"call_id": "C333333", "turn_no": out["turn_no"], "step": 2, "wait_s": 5})
    assert n["segments"][0]["speaker"] == "nexus" and n["segments"][0]["reply_to"] == "hermes" and n["more"] is False
    assert "Hermes fragt dich jetzt direkt" in e.talk[-1][-1]["content"]
    speakers = [x["speaker"] for x in call.load("C333333")["turns"][0]["segments"]]
    assert speakers == ["nexus", "hermes", "nexus"]


def test_no_invented_live_data_in_prompts():
    assert "KEINE Live-Daten" in call.NEXUS_SYSTEM and "KEINE Live-Daten" in call.HERMES_SYSTEM


def test_phone_command_returns_without_model_call():
    e = FakeEngine()
    code, out = call.turn(e, {"call_id": "C444444", "text": "Öffne bitte YouTube"})
    assert out["phone_command"] == "Öffne bitte YouTube" and not e.calls and not e.talk
    code, out = call.turn(e, {"call_id": "C444444", "text": "Öffne bitte YouTube", "force_talk": True})
    assert "phone_command" not in out and e.talk


def test_weather_uses_live_data_not_model(monkeypatch):
    from nexus.voice import live
    monkeypatch.setattr(live, "weather", lambda text, default_place=None: {
        "success": True, "found": True, "place": "Erfurt (Thüringen)", "source": "open-meteo.com",
        "spoken": "In Erfurt ist es gerade bedeckt bei 8 Grad."})
    e = FakeEngine()
    code, out = call.turn(e, {"call_id": "C454545", "target": "conference", "text": "Wie ist das Wetter in Erfurt?"})
    assert out["segments"][0]["reply"].startswith("In Erfurt") and out["segments"][0]["source"] == "open-meteo.com"
    assert not e.talk and not e.calls and out["pending"] == [] and out["live"]["source"] == "open-meteo.com"


def test_action_receipt_recorded():
    code, out = call.action({"call_id": "C464646", "text": "Öffne YouTube", "kind": "open", "detail": "com.google.android.youtube",
                             "ok": True, "reply": "Öffne YouTube."})
    assert code == 200 and call.load("C464646")["actions"][0]["executor"] == "nexusphone_app_parser"


def test_live_place_parsing():
    from nexus.voice import live
    assert live.place_of("Wie ist das Wetter in Friedersdorf in Thüringen?") == "Friedersdorf"
    assert live.place_of("Wie ist das Wetter?") is None
    assert live.is_weather("Regnet es heute?") and not live.is_weather("Öffne YouTube")



def test_guard_replaces_invented_weather_and_actions():
    ok, why = call.guard("In Erfurt ist heute wunderschönes Wetter. Es ist sonnig und warm.", False)
    assert why == "unverified_weather" and "sonnig" not in ok
    assert call.guard("Gut, Eddy. NEXUS hat die App geöffnet.", False)[1] == "unverified_action"
    assert call.guard("Okay, ich öffne YouTube für dich.", False)[1] == "unverified_action"
    assert call.guard("Mir geht es gut, danke!", False) == ("Mir geht es gut, danke!", None)


def test_guarded_reply_is_spoken_and_original_kept():
    e = FakeEngine()

    class LLM:
        def chat(self, messages, **kw):
            return {"success": True, "reply": "Es ist sonnig und warm in Erfurt.", "model": "qwen"}

    e.master.llm = LLM()
    code, out = call.turn(e, {"call_id": "C565656", "text": "Erzähl was"})
    seg = out["segments"][0]
    assert seg["guarded"] == "unverified_weather" and "sonnig" not in seg["spoken"]
    rec = call.load("C565656")["turns"][0]["segments"][0]
    assert rec["original"].startswith("Es ist sonnig") and rec["guarded"] == "unverified_weather"


def test_place_follow_up_after_weather_question(monkeypatch):
    from nexus.voice import live
    seen = []

    def weather(text, default_place=None):
        seen.append(text)
        if "Erfurt" in text:
            return {"success": True, "found": True, "place": "Erfurt", "source": "open-meteo.com",
                    "spoken": "In Erfurt ist es gerade bedeckt bei 8 Grad."}
        return {"success": True, "found": False, "place": "X", "spoken": "Den Ort finde ich nicht."}

    monkeypatch.setattr(live, "weather", weather)
    e = FakeEngine()
    call.turn(e, {"call_id": "C575757", "text": "Wie wird das Wetter in Xyzdorf?"})
    code, out = call.turn(e, {"call_id": "C575757", "text": "Erfurt."})
    assert seen[-1] == "Wetter in Erfurt." and out["segments"][0]["source"] == "open-meteo.com" and not e.talk
    assert not call.load("C575757").get("await_place")


def test_phone_command_sentence_forms():
    assert call.phone_command("Auf ne YouTube.")
    assert call.phone_command("Nein, einfach nur öffnen, die App YouTube.")
    assert not call.phone_command("Erfurt.")


def test_live_place_with_misheard_state():
    from nexus.voice import live
    assert live.place_of("Wie wird das Wetter in Friedersdorf-Türingen?") == "Friedersdorf"
    assert live.state_in("Friedersdorf-Türingen") == "Thüringen"



class FakeOS:
    def __init__(self):
        self.calls = []

    def launch_app(self, name):
        self.calls.append(("launch", name))
        return {"success": True, "action": "launch_app", "app": name, "verified": None}

    def open_url(self, url):
        self.calls.append(("url", url))
        return {"success": True, "action": "open_url", "target": url}

    def volume(self, action, steps):
        self.calls.append(("volume", action))
        return {"success": True, "volume_action": action}

    def close_app(self, name):
        self.calls.append(("close", name))
        return {"success": True, "app": name}


def _pc_engine():
    e = FakeEngine()
    e.master.os_actions = FakeOS()
    return e


def test_pc_open_app_executes_and_records_evidence():
    e = _pc_engine()
    code, out = call.turn(e, {"call_id": "C600001", "text": "Öffne Chrome am PC"})
    assert e.master.os_actions.calls == [("launch", "chrome")]
    assert out["pc_action"]["op"] == "open_app" and out["pc_action"]["ok"] and "phone_command" not in out
    assert not e.talk and not e.calls
    act = call.load("C600001")["actions"][0]
    assert act["executor"] == "pc_os_actions" and act["kind"] == "pc_open_app" and act["evidence"]["success"]


def test_pc_search_url_is_encoded_without_shell_metacharacters():
    e = _pc_engine()
    call.turn(e, {"call_id": "C600002", "text": "Such im Internet nach Tom & Jerry"})
    url = e.master.os_actions.calls[0][1]
    assert url.startswith("https://www.google.com/search?q=") and "&" not in url


def test_pc_close_requires_confirmation():
    e = _pc_engine()
    code, out = call.turn(e, {"call_id": "C600003", "text": "Schließ Spotify am PC"})
    assert out["pc_action"]["op"] == "confirm_request" and e.master.os_actions.calls == []
    code, out = call.turn(e, {"call_id": "C600003", "text": "Ja, mach das"})
    assert e.master.os_actions.calls == [("close", "spotify")] and out["pc_action"]["op"] == "close_app"
    call.turn(e, {"call_id": "C600003", "text": "Schließ Chrome am PC"})
    call.turn(e, {"call_id": "C600003", "text": "Nein"})
    assert ("close", "chrome") not in e.master.os_actions.calls


def test_pc_blocked_and_unknown_never_execute():
    e = _pc_engine()
    for t in ("Öffne PowerShell am PC", "Fahr den PC herunter", "Öffne Photoshop am PC", "Öffne cmd am Rechner"):
        code, out = call.turn(e, {"call_id": "C600004", "text": t})
        assert out["pc_action"]["op"] in ("blocked", "unknown_app") and not out["pc_action"]["ok"]
    assert e.master.os_actions.calls == []
    assert any(a["kind"] == "pc_blocked" for a in call.load("C600004")["actions"])


def test_pc_reply_not_overwritten_by_guard():
    e = _pc_engine()
    code, out = call.turn(e, {"call_id": "C600005", "text": "Such am PC nach Bürgeramt Erfurt"})
    assert "gesucht" in out["segments"][0]["reply"] and not out["segments"][0].get("guarded")
