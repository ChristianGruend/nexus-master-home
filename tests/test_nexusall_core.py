from pathlib import Path
from nexus.nexusall import core

STATIC = Path(__file__).resolve().parents[1] / "nexus" / "chat" / "static"


def test_voice_intent():
    assert core.voice_intent("lade nur den ton von https://x.org/a")["quality"] == "audio"
    assert core.voice_intent("lade das video herunter")["action"] == "download"
    assert core.voice_intent("öffne nexus all") == {"action": "open"}
    assert core.voice_intent("hallo") is None


def test_url_validation():
    assert core._valid_url("https://download.blender.org/a.mp4")
    assert not core._valid_url("file:///C:/x")
    assert not core._valid_url("javascript:alert(1)")
    assert core.enqueue("ftp://x")["success"] is False


def test_resolve_blocks_traversal():
    assert core.resolve("../../nexus/common/config.py") is None


def test_tile_and_telemetry_pin():
    js = (STATIC / "js" / "master_glass.js").read_text(encoding="utf-8")
    assert '"nexusall"' in js and 'window.open("/nexusall"' in js
    assert "cp.before(tl)" in js
    assert (STATIC / "nexusall.html").exists()
    idx = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "master_glass.js?v=" in idx


def test_enqueue_rejects_non_media(monkeypatch):
    monkeypatch.setattr(core, "media_check", lambda u: {"ok": False, "detail": "Unsupported URL"})
    r = core.enqueue("https://duckduckgo.com/html/?q=x")
    assert r["success"] is False and r["error"] == "NO_MEDIA"
    assert r["message"] == "Keine Mediendatei auf dieser Seite"
