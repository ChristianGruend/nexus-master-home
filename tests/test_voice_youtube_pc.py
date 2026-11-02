import pytest
from nexus.computer.os_actions import OSActions


@pytest.mark.parametrize("name", ["youtube am pc", "YouTube auf dem PC", "youtube am rechner bitte"])
def test_voice_youtube_am_pc_opens_youtube_url(monkeypatch, name):
    calls = []
    a = OSActions()
    monkeypatch.setattr(a, "open_url", lambda u, *k, **kw: calls.append(u) or {"success": True, "ok": True, "url": u})
    monkeypatch.setattr(a, "_start_by_name", lambda n: pytest.fail("darf nicht per Name starten: " + n))
    r = a.launch_app(name)
    assert r.get("success") is True
    assert any("youtube.com" in u for u in calls)


def test_unknown_app_reports_honest_error(monkeypatch):
    a = OSActions()
    monkeypatch.setattr(a, "_start_by_name", lambda n: {"ok": False, "error": "not_found", "target": n})
    r = a.launch_app("youtubee")
    assert r["success"] is False
    assert "gestartet" not in r["reply"]
    assert "nicht gefunden" in r["reply"]
    assert "youtube" in r.get("suggestions", [])


def test_start_by_name_real_nonexistent_is_not_ok():
    import sys
    if sys.platform != "win32":
        pytest.skip("nur Windows")
    r = OSActions()._start_by_name("nexus_gibt_es_nicht_xyz123")
    assert r["ok"] is False


def test_no_suggestions_without_real_similarity():
    from nexus.computer.os_actions import _suggest_apps, _not_found_reply
    assert _suggest_apps("fantasieapp123") == []
    assert "Meintest" not in _not_found_reply("fantasieapp123")


def test_controller_ok_propagates_failed_os_action():
    from nexus.master.controller import MasterController
    out = MasterController._ok(object.__new__(MasterController), "App x nicht gefunden.", "os_launch_app", [], data={"success": False, "error": "APP_NOT_FOUND"})
    assert out["success"] is False and out["error"] == "APP_NOT_FOUND"
    ok = MasterController._ok(object.__new__(MasterController), "App geoeffnet: youtube", "os_launch_app", [], data={"success": True})
    assert ok["success"] is True
