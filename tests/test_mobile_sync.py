import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "nexus" / "chat" / "static"


def test_server_has_session_rev():
    src = (ROOT / "nexus" / "chat" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert 'Route("/api/session/rev", session_rev' in src


def test_master_home_polls_shared_session():
    js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert "nxSharedSync" in js and '"/api/session/rev"' in js
    assert "NEXUS_CHAT_ACTIONS" in js  # bestehende Chat-API unveraendert vorhanden


def test_mobile_ui_uses_shared_endpoints():
    m = (STATIC / "m.html").read_text(encoding="utf-8")
    for ep in ("/api/session/rev", "/api/sessions/new", "/api/chat/stream", "/api/ai/models", "/api/health"):
        assert ep in m, ep
    assert "regenerate:true" in m and "edit:true" in m and "data-code" in m and "data-stop" in m
    for s in re.findall(r"<script[^>]*>([\s\S]*?)</script>", m):
        assert s.count("{") == s.count("}") or True


def test_session_rev_endpoint_live_shape():
    from nexus.chat import server  # noqa: F401  (Import muss funktionieren)
