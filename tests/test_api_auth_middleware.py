"""Stufe 1.2: Zugangsprüfung in der echten App (Starlette-Middleware, ohne Serverstart)."""
from __future__ import annotations

import json

import pytest

from nexus.common import api_auth
from tests._http import LOCAL, REMOTE, nexus_client


@pytest.fixture(autouse=True)
def _tmp_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(api_auth, "_secrets_dir", lambda: tmp_path / "secrets")
    monkeypatch.delenv("NEXUS_API_TOKEN", raising=False)


def _req(path, ip=REMOTE, method="GET", headers=None):
    with nexus_client(ip) as c:
        r = c.request(method, path, headers=headers, content=b"{}" if method == "POST" else None, follow_redirects=False)
    return r.status_code, r.headers, r.content


def test_remote_api_without_credentials_is_401():
    code, _, body = _req("/api/memory")
    assert code == 401 and json.loads(body)["error"] == "AUTH_REQUIRED"


def test_remote_post_without_credentials_is_401():
    assert _req("/api/shell/run", method="POST")[0] == 401


def test_remote_media_without_credentials_is_401():
    assert _req("/media/x.mp4")[0] == 401


def test_loopback_passes_middleware():
    assert _req("/api/does-not-exist", ip=LOCAL)[0] == 404


def test_loopback_with_proxy_header_is_not_loopback():
    assert _req("/api/memory", ip=LOCAL, headers={"X-Forwarded-For": "127.0.0.1"})[0] == 401


def test_remote_with_bearer_passes():
    code = _req("/api/does-not-exist", headers={"Authorization": "Bearer " + api_auth.api_token()})[0]
    assert code == 404


def test_cross_site_get_from_loopback_is_403():
    assert _req("/api/memory", ip=LOCAL, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403


def test_health_is_public():
    assert _req("/api/health")[0] == 200


def test_login_sets_cookie_and_cookie_grants_access():
    code, hdrs, _ = _req("/login?token=" + api_auth.api_token() + "&next=/m")
    assert code == 303 and hdrs["location"] == "/m"
    cookie = hdrs["set-cookie"].split(";")[0]
    assert cookie.startswith("nx_session=") and "HttpOnly" in hdrs["set-cookie"]
    assert _req("/api/does-not-exist", headers={"Cookie": cookie})[0] == 404


def test_login_rejects_open_redirect():
    _, hdrs, _ = _req("/login?token=" + api_auth.api_token() + "&next=//evil.example")
    assert hdrs["location"] == "/"


def test_login_wrong_token():
    code, hdrs, _ = _req("/login?token=falsch")
    assert code == 401 and "set-cookie" not in hdrs


def test_json_responses_have_security_headers():
    _, hdrs, _ = _req("/api/does-not-exist", ip=LOCAL)
    assert hdrs.get("referrer-policy") == "no-referrer"
    assert hdrs.get("x-content-type-options") == "nosniff"


def test_ui_pages_have_csp():
    code, hdrs, _ = _req("/", ip=REMOTE)
    assert code == 200 and "frame-ancestors 'self'" in hdrs["content-security-policy"]
    assert hdrs["cache-control"] == "no-cache"


def test_login_form_keeps_escaped_next():
    _, _, body = _req("/login?next=/m%27%3E%3Cscript%3E")
    html = body.decode()
    assert "<script>" not in html and "value='/m&#x27;&gt;&lt;script&gt;'" in html
