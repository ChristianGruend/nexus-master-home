"""2026-10-26 BLOCKED-Fix: Eddies echte UI-Saetze (mit Antwortvorgabe-Suffix) duerfen nicht BLOCKED enden."""
from nexus.master.decision import decide, strip_answer_policy, web_lookup_query
from nexus.master.tools import parse_tool_calls
from nexus.common.ollama_client import _safe_messages

SUFFIX = ("\n\n[Antwortvorgabe: Antworte standardmaessig kurz, praezise und nur mit den notwendigen "
          "Informationen. Wenn der Nutzer ausdruecklich Details oder eine ausfuehrliche Erklaerung "
          "verlangt, darfst du entsprechend ausfuehrlich antworten.]")


def test_strip_answer_policy():
    assert strip_answer_policy("hallo" + SUFFIX) == "hallo"
    assert strip_answer_policy("ohne suffix") == "ohne suffix"


def test_crew_query_has_no_suffix():
    d = decide("@Fixer behebe den fehler" + SUFFIX)
    assert d["intent"] == "crew" and d["agent_id"] == "fixer" and d["query"] == "behebe den fehler"


def test_web_typo_netzt_and_suffix():
    d = decide("such mal im netzt nach dem schwarzen oktober php" + SUFFIX)
    assert d["intent"] == "research" and d["query"] == "schwarzen oktober php"


def test_youtube_link_is_web_lookup():
    assert web_lookup_query("zeig mir ein link für youtube zu katzen") == "youtube zu katzen"


def test_route_work_crew_not_blocked():
    from nexus.master.evidence import route_work
    out = route_work(None, "@Coder zeig mir ein link für youtube mit dessen thema" + SUFFIX, [], {"id": "t"})
    assert out is None  # faellt auf normale Crew-Ausfuehrung durch, kein BLOCKED


def test_shell_stays_blocked():
    from nexus.master.evidence import _unhandled_capability
    assert _unhandled_capability("@Ops fuehre powershell aus").startswith("geprüfter")


def test_tool_call_tags_sanitized_and_parsed():
    msgs = _safe_messages([{"role": "system", "content": 'Format: <tool_call>{"name":"x","args":{}}</tool_call>'}])
    assert "<tool_call>" not in msgs[0]["content"] and "<tool-call>" in msgs[0]["content"]
    calls = parse_tool_calls('<tool-call>{"name":"read_file","args":{"path":"a.txt"}}</tool-call>')
    assert calls and calls[0]["name"] == "read_file"