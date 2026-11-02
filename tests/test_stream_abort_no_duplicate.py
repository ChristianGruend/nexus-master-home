"""P4: Kein doppelter Schreibauftrag, wenn der Chat-Stream abbricht.

Zwei Wege duerfen niemals erneut senden:
  1. Server: _chat_stream darf nach einem Abbruch den Arbeitstrack nicht
     ein zweites Mal starten (ein Auftrag, genau ein Start).
  2. Client: sendStream darf nach Verbindungsabbruch nicht implizit auf
     /api/chat zurueckfallen (dort wird geschrieben, ohne Stream-Nachweis).
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "nexus" / "chat" / "server.py"
APP_JS = ROOT / "nexus" / "chat" / "static" / "js" / "app.js"


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f" Funktion {name} nicht gefunden")


def _called_names(fn: ast.FunctionDef) -> list[str]:
    names = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            names.append(func.attr)
        elif isinstance(func, ast.Name):
            names.append(func.id)
    return names


class StreamAbortNoDuplicate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server_src = SERVER.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.server_src)
        cls.chat_stream_fn = _function(cls.tree, "_chat_stream")
        cls.app_js = APP_JS.read_text(encoding="utf-8")

    def test_server_starts_work_exactly_once_per_request(self):
        calls = [n for n in _called_names(self.chat_stream_fn) if n == "chat_stream"]
        self.assertEqual(len(calls), 1, "ein Request -> genau ein Start des Arbeitstracks")

    def test_server_never_restarts_work_in_an_error_branch(self):
        for handler in ast.walk(self.chat_stream_fn):
            if not isinstance(handler, ast.ExceptHandler):
                continue
            label = ast.unparse(handler.type) if handler.type else "bare"
            starts = [n for n in _called_in(handler.body) if n == "chat_stream"]
            self.assertEqual(
                starts, [], f"Fehlerpfad ({label}) darf den Arbeitstrack nicht neu starten"
            )

    def test_client_does_not_resubmit_after_stream_interrupt(self):
        segment = self._client_segment()
        self.assertIn("STREAM_INTERRUPTED_NO_RESUBMIT", segment)
        self.assertIn("Never resubmit implicitly", segment)
        # Der alte Fallback auf den schreibenden Nicht-Stream-Endpunkt darf nicht zurück.
        self.assertNotIn('"/api/chat"', segment, "impliziter Non-Stream-Nachsendeversuch verboten")

    def test_client_keeps_abort_separate_from_failure(self):
        segment = self._client_segment()
        self.assertIn("AbortError", segment)
        self.assertIn("aborted: true", segment)

    def _client_segment(self) -> str:
        start = self.app_js.index("async function sendStream(")
        end = self.app_js.index('window.addEventListener("nexus:send"', start)
        return self.app_js[start:end]


def _called_in(statements) -> list[str]:
    names = []
    for stmt in statements:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                names.append(func.attr)
            elif isinstance(func, ast.Name):
                names.append(func.id)
    return names


if __name__ == "__main__":
    unittest.main()
