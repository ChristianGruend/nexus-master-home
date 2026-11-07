"""Stufe 2.1a: Bridge-Freigaben nur durch einen Menschen im Browser-UI, nie allein per Bridge-Token."""
import pytest

from nexus.bridge import api as bridge_api
from nexus.common import api_auth
from nexus.terminal import guard


class FakeHandler:
    def __init__(self, headers):
        self.headers = headers
        self.client_address = ("127.0.0.1", 1)
        self.sent = None

    def _json(self, code, obj):
        self.sent = (code, obj)


@pytest.fixture
def secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(guard, "ui_secret", lambda: "u" * 40)
    monkeypatch.setattr(api_auth, "session_secret", lambda: "s" * 40)
    monkeypatch.setattr(bridge_api, "require_auth", lambda h: True)  # Bridge-Token gilt als vorhanden
    monkeypatch.setattr(guard, "audit", lambda *a, **k: None)
    decided = []

    class FakeStore:
        def decide(self, aid, approve, decided_by):
            decided.append((aid, approve))

            class R:
                def to_dict(self_inner):
                    return {"id": aid, "approved": approve}
            return R()

    import nexus.bridge.approvals as appr
    monkeypatch.setattr(appr, "approvals", lambda: FakeStore())
    return decided


UI = {"Sec-Fetch-Site": "same-origin", "X-NEXUS-UI": "approve"}


@pytest.mark.parametrize("headers,ok", [
    ({**UI, "Cookie": "nx_term_ui=" + "u" * 40}, True),
    ({**UI, "Cookie": "nx_session=" + "s" * 40}, True),
    ({**UI, "Cookie": "a=b; nx_term_ui=" + "u" * 40}, True),
    ({**UI}, False),                                                     # kein Cookie
    ({**UI, "Cookie": "nx_term_ui=falsch"}, False),
    ({"X-NEXUS-UI": "approve", "Cookie": "nx_term_ui=" + "u" * 40}, False),  # curl ohne Sec-Fetch-Site
    ({"Sec-Fetch-Site": "same-origin", "Cookie": "nx_term_ui=" + "u" * 40}, False),  # ohne UI-Header
    ({**UI, "Sec-Fetch-Site": "cross-site", "Cookie": "nx_term_ui=" + "u" * 40}, False),
])
def test_human_approval(secrets, headers, ok):
    assert api_auth.human_approval(headers)[0] is ok


def test_bridge_token_alone_cannot_approve(secrets):
    h = FakeHandler({"Authorization": "Bearer bridge-token"})
    bridge_api.handle_post(h, "/api/bridge/approvals/abc/approve", {"by": "agent"})
    assert h.sent[0] == 403 and h.sent[1]["error"] == "HUMAN_APPROVAL_REQUIRED"
    assert secrets == []


def test_bridge_token_alone_cannot_deny(secrets):
    h = FakeHandler({"Authorization": "Bearer bridge-token"})
    bridge_api.handle_post(h, "/api/bridge/approvals/abc/deny", {})
    assert h.sent[0] == 403 and secrets == []


def test_ui_can_approve(secrets):
    h = FakeHandler({**UI, "Cookie": "nx_term_ui=" + "u" * 40})
    bridge_api.handle_post(h, "/api/bridge/approvals/abc/approve", {"by": "local_ui"})
    assert h.sent[0] == 200 and secrets == [("abc", True)]


def test_ui_cookie_scope_covers_bridge():
    assert "Path=/api;" in guard.ui_cookie() and "HttpOnly" in guard.ui_cookie()
