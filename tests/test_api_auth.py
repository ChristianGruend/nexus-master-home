import os
import stat

import pytest

from nexus.common import api_auth


@pytest.fixture
def secrets_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(api_auth, "_secrets_dir", lambda: tmp_path / "secrets")
    monkeypatch.delenv("NEXUS_API_TOKEN", raising=False)
    yield


def test_secret_created_and_stable(secrets_tmp):
    first = api_auth.session_secret()
    assert len(first) >= 32
    second = api_auth.session_secret()
    assert first == second


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_secret_file_mode(secrets_tmp):
    api_auth.session_secret()
    path = api_auth._secrets_dir() / "api_session.txt"
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_api_token_env(secrets_tmp, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", "x" * 40)
    assert api_auth.api_token() == "x" * 40


def test_api_token_env_too_short(secrets_tmp, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", "short")
    token = api_auth.api_token()
    assert len(token) >= 32


def test_has_session_valid(secrets_tmp):
    cookie = f"nx_session={api_auth.session_secret()}"
    assert api_auth.has_session(cookie) is True


def test_has_session_among_others(secrets_tmp):
    cookie = f"a=1; nx_session={api_auth.session_secret()}; b=2"
    assert api_auth.has_session(cookie) is True


def test_has_session_invalid(secrets_tmp):
    assert api_auth.has_session("nx_session=wrong") is False


def test_has_session_none(secrets_tmp):
    assert api_auth.has_session(None) is False


def test_session_cookie_default(secrets_tmp):
    cookie = api_auth.session_cookie()
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie


def test_session_cookie_secure(secrets_tmp):
    cookie = api_auth.session_cookie(secure=True)
    assert "; Secure" in cookie


def test_check_public_static(secrets_tmp):
    ok, reason = api_auth.check("/static/app.js", "100.64.0.5", {})
    assert (ok, reason) == (True, "PUBLIC")


def test_check_public_health(secrets_tmp):
    ok, reason = api_auth.check("/api/health", "100.64.0.5", {})
    assert (ok, reason) == (True, "PUBLIC")


def test_check_own_auth(secrets_tmp):
    ok, reason = api_auth.check("/api/nexus-os/sync", "100.64.0.5", {})
    assert (ok, reason) == (True, "OWN_AUTH")


def test_check_auth_required_foreign(secrets_tmp):
    ok, reason = api_auth.check("/api/shell/run", "100.64.0.5", {})
    assert (ok, reason) == (False, "AUTH_REQUIRED")


def test_check_loopback(secrets_tmp):
    ok, reason = api_auth.check("/api/shell/run", "127.0.0.1", {})
    assert (ok, reason) == (True, "LOOPBACK")


def test_check_proxy_header_blocks_loopback(secrets_tmp):
    ok, reason = api_auth.check("/api/shell/run", "127.0.0.1", {"X-Forwarded-For": "1.2.3.4"})
    assert (ok, reason) == (False, "AUTH_REQUIRED")


def test_check_cross_site(secrets_tmp):
    ok, reason = api_auth.check("/api/shell/run", "127.0.0.1", {"Sec-Fetch-Site": "cross-site"})
    assert (ok, reason) == (False, "CROSS_SITE")


def test_check_session_cookie(secrets_tmp):
    cookie = f"nx_session={api_auth.session_secret()}"
    ok, reason = api_auth.check("/media/x.mp4", "100.64.0.5", {"Cookie": cookie})
    assert (ok, reason) == (True, "SESSION")


def test_check_token_valid(secrets_tmp):
    token = api_auth.api_token()
    ok, reason = api_auth.check("/api/chat", "100.64.0.5", {"Authorization": "Bearer " + token})
    assert (ok, reason) == (True, "TOKEN")


def test_check_token_invalid(secrets_tmp):
    ok, reason = api_auth.check("/api/chat", "100.64.0.5", {"Authorization": "Bearer wrong"})
    assert (ok, reason) == (False, "AUTH_REQUIRED")
