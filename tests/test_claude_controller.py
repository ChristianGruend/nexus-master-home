from __future__ import annotations

import unittest
from types import SimpleNamespace

from nexus.master.controller import MasterController


class FakeAgent:
    client = SimpleNamespace(api_key="")

    def status(self):
        return {"success": True, "configured": False, "model": "fake", "task_count": 0,
                "data": {"provider": "claude"}}

    def tasks(self, limit=20):
        return []

    def run(self, message, context=None, **kwargs):
        return {"success": True, "reply": "ok", "intent": "claude"}


class ClaudeDispatchTests(unittest.TestCase):
    def _controller(self):
        ctrl = MasterController.__new__(MasterController)
        ctrl._claude_agent = FakeAgent()
        return ctrl

    def test_normal_message_is_not_intercepted(self) -> None:
        self.assertIsNone(self._controller()._claude_dispatch("baue mir ein skript"))

    def test_explicit_status(self) -> None:
        out = self._controller()._claude_dispatch("claude status")
        self.assertIsNotNone(out)
        self.assertEqual(out["intent"], "claude_status")
        self.assertIn("nicht konfiguriert", out["reply"])

    def test_frag_claude_prefix(self) -> None:
        out = self._controller()._claude_dispatch("frag claude status")
        self.assertEqual(out["intent"], "claude_status")

    def test_missing_key_blocks_run(self) -> None:
        out = self._controller()._claude_dispatch("claude: teste das")
        self.assertEqual(out["error"], "claude_not_configured")

    def test_generic_prefix_is_intercepted(self) -> None:
        out = self._controller()._claude_dispatch("claude teste das")
        self.assertEqual(out["error"], "claude_not_configured")


if __name__ == "__main__":
    unittest.main()
