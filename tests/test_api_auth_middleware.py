"""Stufe 1.2: Zugangsprüfung im echten Handler (ohne Serverstart, wie test_http_client_disconnect)."""
from __future__ import annotations

import ast
import io
import json
import sys
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from nexus.common import api_auth

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "100.64.0.5"


@pytest.fixture
def handler_cls(tmp_path, monkeypatch):
    monkeypatch.setattr(api_auth, "_secrets_dir", lambda: tmp_path / "secrets")
    monkeypatch.delenv("NEXUS_API_TOKEN", raising=False)
    source = ROOT / "nexus" / "chat" / "server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
             and n.name in ("_ClientDisconnected", "_BadRequest", "_host_allowed", "Handler")]
    scope = {"BaseHTTPRequestHandler": BaseHTTPRequestHandler, "Path": Path, "json": json, "sys": sys,
             "parse_qs": parse_qs, "urlparse": urlparse, "traceback": SimpleNamespace(print_exc=Mock()),
             "ENGINE": SimpleNamespace(health=Mock(return_value={"success": True})),
             "bridge_api": None, "automation_api": None, "multimodal_api": None,
             "_ALLOWED_HOSTS": frozenset(), "_MAX_BODY_BYTES": 1 << 20, "api_auth": api_auth,
             "_UI_CSP": "default-src 'self'"}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
    nexus = ModuleType("nexus")
    nexus.nexus_os = SimpleNamespace(note_client=Mock())
    nexus.nexus_os_link = SimpleNamespace(handle=Mock(return_value=None))
    maintenance = ModuleType("nexus.maintenance")
    maintenance.MaintenanceActive = type("MaintenanceActive", (Exception,), {})
    maintenance.block_payload = lambda: {"error": "maintenance"}
    maintenance.maintenance = lambda: SimpleNamespace(is_draining=lambda: False, work=lambda name: nullcontext())
    with patch.dict(sys.modules, {"nexus": nexus, "nexus.maintenance": maintenance}):
        yield scope["Handler"]


def _req(cls, path, ip=REMOTE, method="GET", headers=None):
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    h = cls.__new__(cls)
    h.rfile = io.BytesIO((f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n{extra}"
                          "Connection: close\r\nContent-Length: 2\r\n\r\n{}").encode())
    h.wfile = io.BytesIO()
    h.client_address = (ip, 12345)
    h.server = SimpleNamespace(server_name="localhost", server_port=8299)
    h.log_message = Mock()
    h.handle_one_request()
    head, _, body = h.wfile.getvalue().partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    hdrs = {k.lower(): v for k, _, v in (ln.partition(": ") for ln in lines[1:])}
    return int(lines[0].split()[1]), hdrs, body


def test_remote_api_without_credentials_is_401(handler_cls):
    code, _, body = _req(handler_cls, "/api/memory")
    assert code == 401 and json.loads(body)["error"] == "AUTH_REQUIRED"


def test_remote_post_without_credentials_is_401(handler_cls):
    assert _req(handler_cls, "/api/shell/run", method="POST")[0] == 401


def test_remote_media_without_credentials_is_401(handler_cls):
    assert _req(handler_cls, "/media/x.mp4")[0] == 401


def test_loopback_passes_middleware(handler_cls):
    assert _req(handler_cls, "/api/does-not-exist", ip="127.0.0.1")[0] == 404


def test_remote_with_bearer_passes(handler_cls):
    code = _req(handler_cls, "/api/does-not-exist", headers={"Authorization": "Bearer " + api_auth.api_token()})[0]
    assert code == 404


def test_cross_site_get_from_loopback_is_403(handler_cls):
    assert _req(handler_cls, "/api/memory", ip="127.0.0.1", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403


def test_health_is_public(handler_cls):
    assert _req(handler_cls, "/api/health")[0] == 200


def test_login_sets_cookie_and_cookie_grants_access(handler_cls):
    code, hdrs, _ = _req(handler_cls, "/login?token=" + api_auth.api_token() + "&next=/m")
    assert code == 303 and hdrs["location"] == "/m"
    cookie = hdrs["set-cookie"].split(";")[0]
    assert cookie.startswith("nx_session=") and "HttpOnly" in hdrs["set-cookie"]
    assert _req(handler_cls, "/api/does-not-exist", headers={"Cookie": cookie})[0] == 404


def test_login_rejects_open_redirect(handler_cls):
    _, hdrs, _ = _req(handler_cls, "/login?token=" + api_auth.api_token() + "&next=//evil.example")
    assert hdrs["location"] == "/"


def test_login_wrong_token(handler_cls):
    code, hdrs, _ = _req(handler_cls, "/login?token=falsch")
    assert code == 401 and "set-cookie" not in hdrs


def test_json_responses_have_security_headers(handler_cls):
    _, hdrs, _ = _req(handler_cls, "/api/does-not-exist", ip="127.0.0.1")
    assert hdrs.get("referrer-policy") == "no-referrer"
    assert hdrs.get("x-content-type-options") == "nosniff"


def test_login_form_keeps_escaped_next(handler_cls):
    _, _, body = _req(handler_cls, "/login?next=/m%27%3E%3Cscript%3E")
    html = body.decode()
    assert "<script>" not in html and "value='/m&#x27;&gt;&lt;script&gt;'" in html
