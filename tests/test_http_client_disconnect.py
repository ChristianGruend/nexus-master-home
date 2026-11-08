"""HTTP-Regressionen rund um Fehler und Abbruch (Stufe 4.6: Starlette, ohne Serverstart).

Das Schreiben auf den Socket und Verbindungsabbrüche übernimmt uvicorn; geprüft wird hier, was die App
dabei tun muss: Fehler generisch mit Request-ID, Stream-Abbruch schließt die Arbeit ohne Neustart und ohne
Fehlerereignis.
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests._http import nexus_client


class HttpErrorAndAbortTests(unittest.TestCase):
    def setUp(self):
        self.engine = SimpleNamespace(health=Mock(return_value={"success": True}), session_id="CHAT-test",
                                      chat_stream=Mock(side_effect=self._stream))
        self.closed = []

    def _stream(self, *args, **kwargs):
        try:
            yield {"type": "token", "text": "hello"}
            yield {"type": "done", "reply": "hello"}
        finally:
            self.closed.append(True)

    def test_successful_response_keeps_json_body(self):
        with nexus_client(engine=self.engine) as c:
            r = c.get("/api/health")
        self.assertEqual((r.status_code, r.json()), (200, {"success": True}))

    def test_unknown_post_is_404_json(self):
        with nexus_client(engine=self.engine) as c:
            r = c.post("/unknown", content=b"{}")
        self.assertEqual((r.status_code, r.json()), (404, {"success": False, "error": "not_found", "path": "/unknown"}))

    def test_application_error_still_sends_generic_500_with_request_id(self):
        # Auch ein ConnectionAbortedError aus der Anwendung ist ein Serverfehler, kein Client-Abbruch.
        with patch("nexus.chat.server.traceback.print_exc") as print_exc:
            for error in (RuntimeError, ConnectionAbortedError):
                with self.subTest(error=error.__name__):
                    self.engine.health.side_effect = error("backend failed")
                    with nexus_client(engine=self.engine) as c:
                        r = c.get("/api/health")
                    self.assertEqual(r.status_code, 500)
                    out = r.json()
                    self.assertEqual({k: out[k] for k in ("success", "error")}, {"success": False, "error": "INTERNAL_ERROR"})
                    self.assertRegex(out["request_id"], r"^[0-9a-f]{12}$")
                    self.assertNotIn("backend failed", r.text)
        self.assertEqual(print_exc.call_count, 2)

    def test_stream_delivers_tokens_done_and_end_marker(self):
        with nexus_client(engine=self.engine) as c:
            r = c.post("/api/chat/stream", json={"message": "hi"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))
        events = [ln[6:] for ln in r.text.split("\n\n") if ln.startswith("data: ")]
        self.assertEqual(json.loads(events[0]), {"type": "token", "text": "hello"})
        self.assertEqual(json.loads(events[1])["type"], "done")
        self.assertEqual(events[-1], "[DONE]")
        self.assertEqual(self.engine.chat_stream.call_count, 1)

    def test_stream_abort_closes_work_without_restart_or_error_event(self):
        from nexus.chat.routes import chat
        with patch.object(chat, "ENGINE", self.engine), patch("traceback.print_exc") as print_exc:
            gen = chat._chat_stream({"message": "hi"})
            first = next(gen)
            gen.close()  # so schließt Starlette den Generator, wenn der Client weg ist
        self.assertIn('"token"', first)
        self.assertEqual(self.engine.chat_stream.call_count, 1)
        self.assertEqual(self.closed, [True])
        print_exc.assert_not_called()

    def test_maintenance_routes_reach_bridge_module(self):
        from nexus.chat.routes import bridge
        fake = SimpleNamespace(is_maintenance_path=lambda path: True,
                               handle_maintenance_get=lambda h, path, qs: h._json(200, {"m": "get"}),
                               handle_maintenance=lambda h, path, body: h._json(200, {"m": "post"}))
        with patch.object(bridge, "bridge_api", fake), nexus_client() as c:
            self.assertEqual(c.get("/api/bridge/maintenance").json(), {"m": "get"})
            self.assertEqual(c.post("/api/bridge/maintenance", json={}).json(), {"m": "post"})


if __name__ == "__main__":
    unittest.main()
