"""HTTP disconnect regressions, without importing or starting the NEXUS server."""
from __future__ import annotations

import ast
import io
import json
import sys
import unittest
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]


class _ResponseWriter(io.BytesIO):
    def __init__(self, stage=None, error=ConnectionAbortedError):
        super().__init__()
        self.stage = stage
        self.error = error
        self.write_attempts = 0
        self.flush_attempts = 0

    def write(self, data):
        self.write_attempts += 1
        if self.write_attempts == {"headers": 1, "body": 2}.get(self.stage):
            raise self.error("HTTP peer disconnected")
        return super().write(data)

    def flush(self):
        self.flush_attempts += 1
        if self.stage == "flush":
            raise self.error("HTTP peer disconnected")
        return super().flush()


class HttpClientDisconnectTests(unittest.TestCase):
    def setUp(self):
        # Compile the actual handler only: importing server.py starts application
        # components and would make this transport regression test stateful.
        source = ROOT / "nexus" / "chat" / "server.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                 and node.name in ("_ClientDisconnected", "_BadRequest", "_host_allowed", "Handler")]
        self.traceback = SimpleNamespace(print_exc=Mock())
        self.engine = SimpleNamespace(health=Mock(return_value={"success": True}),
                                      chat_stream=Mock(return_value=iter([
                                          {"type": "token", "text": "hello"},
                                          {"type": "done", "reply": "hello"},
                                      ])))
        self.scope = {"BaseHTTPRequestHandler": BaseHTTPRequestHandler,
                      "Path": Path, "json": json, "sys": sys,
                      "parse_qs": parse_qs, "urlparse": urlparse,
                      "traceback": self.traceback, "ENGINE": self.engine,
                      "bridge_api": None, "automation_api": None,
                      "multimodal_api": None,
                      "_ALLOWED_HOSTS": frozenset(), "_MAX_BODY_BYTES": 64 * 1024 * 1024, "_UI_CSP": "default-src 'self'",
                      "api_auth": SimpleNamespace(check=lambda path, ip, headers: (True, "TEST"))}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.scope)
        nexus = ModuleType("nexus")
        nexus.nexus_os = SimpleNamespace(note_client=Mock())
        nexus.nexus_os_link = SimpleNamespace(handle=Mock(return_value=None))
        maintenance = ModuleType("nexus.maintenance")
        maintenance.MaintenanceActive = type("MaintenanceActive", (Exception,), {})
        maintenance.block_payload = lambda: {"error": "maintenance"}
        maintenance.maintenance = lambda: SimpleNamespace(
            is_draining=lambda: False, work=lambda name: nullcontext())
        self.modules = patch.dict(sys.modules, {"nexus": nexus,
                                               "nexus.maintenance": maintenance})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def _request(self, method="GET", path="/api/health", stage=None,
                 error=ConnectionAbortedError):
        handler = self.scope["Handler"].__new__(self.scope["Handler"])
        handler.rfile = io.BytesIO(
            (f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n"
             "Connection: close\r\nContent-Length: 2\r\n\r\n{}").encode("ascii"))
        handler.wfile = _ResponseWriter(stage, error)
        handler.client_address = ("127.0.0.1", 12345)
        handler.server = SimpleNamespace(server_name="localhost", server_port=8299)
        handler.log_message = Mock()
        handler.send_response = Mock(wraps=handler.send_response)
        handler.handle_one_request()
        return handler

    def test_get_disconnect_does_not_attempt_a_second_response(self):
        for error in (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            for stage in ("headers", "body", "flush"):
                with self.subTest(error=error.__name__, stage=stage):
                    handler = self._request(stage=stage, error=error)
                    handler.send_response.assert_called_once_with(200)
                    self.assertEqual(handler.wfile.write_attempts,
                                     1 if stage == "headers" else 2)
                    self.assertTrue(handler.close_connection)
        self.traceback.print_exc.assert_not_called()

    def test_post_disconnect_does_not_attempt_a_second_response(self):
        handler = self._request("POST", "/unknown", stage="body")
        handler.send_response.assert_called_once_with(404)
        self.assertEqual(handler.wfile.write_attempts, 2)
        self.traceback.print_exc.assert_not_called()

    def test_options_header_disconnect_is_handled_at_request_boundary(self):
        handler = self._request("OPTIONS", stage="headers")
        handler.send_response.assert_called_once_with(204)
        self.assertEqual(handler.wfile.write_attempts, 1)

    def test_maintenance_disconnect_does_not_attempt_a_second_response(self):
        self.scope["bridge_api"] = SimpleNamespace(
            is_maintenance_path=lambda path: True,
            handle_maintenance_get=lambda handler, path, qs: handler._json(200, {}),
            handle_maintenance=lambda handler, path, body: handler._json(200, {}))
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                handler = self._request(method, "/api/bridge/maintenance", stage="body")
                handler.send_response.assert_called_once_with(200)
                self.assertEqual(handler.wfile.write_attempts, 2)
        self.traceback.print_exc.assert_not_called()

    def test_successful_response_keeps_json_body(self):
        handler = self._request()
        body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)[1]
        self.assertEqual(json.loads(body), {"success": True})
        handler.send_response.assert_called_once_with(200)

    def test_application_error_still_sends_500(self):
        for error in (RuntimeError, ConnectionAbortedError):
            with self.subTest(error=error.__name__):
                self.engine.health.side_effect = error("backend failed")
                handler = self._request()
                handler.send_response.assert_called_once_with(500)
                body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)[1]
                self.assertEqual(json.loads(body), {"success": False, "error": "backend failed"})
        self.assertEqual(self.traceback.print_exc.call_count, 2)

    def test_stream_disconnect_does_not_restart_work_or_send_error_event(self):
        for stage in ("headers", "body", "flush"):
            with self.subTest(stage=stage):
                self.engine.chat_stream.reset_mock()
                self.engine.chat_stream.return_value = iter([{"type": "token", "text": "hello"}])
                handler = self._request("POST", "/api/chat/stream", stage=stage)
                handler.send_response.assert_called_once_with(200)
                self.assertEqual(handler.wfile.write_attempts, 1 if stage == "headers" else 2)
                self.assertEqual(self.engine.chat_stream.call_count, 0 if stage == "headers" else 1)
        self.traceback.print_exc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
