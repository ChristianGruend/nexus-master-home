from __future__ import annotations

import os
import unittest
from unittest import mock

from nexus.computer import desktop


class DesktopCapabilityTests(unittest.TestCase):
    def test_capabilities_shape(self) -> None:
        caps = desktop.capabilities()
        self.assertTrue(caps["success"])
        self.assertIn("display_ready", caps)
        self.assertIn("input", caps)
        self.assertIn("screenshot_backend", caps)
        self.assertIn("approval_required", caps)

    def test_invalid_clipboard_action(self) -> None:
        with mock.patch.dict(os.environ, {"NEXUS_CLAUDE_DESKTOP_APPROVAL": "0"}):
            out = desktop.clipboard("invalid")
        self.assertFalse(out["success"])
        self.assertEqual(out["error"], "invalid_clipboard_action")

    def test_invalid_media_action(self) -> None:
        with mock.patch.dict(os.environ, {"NEXUS_CLAUDE_DESKTOP_APPROVAL": "0"}):
            out = desktop.media_key("does-not-exist")
        self.assertFalse(out["success"])
        self.assertEqual(out["error"], "invalid_media_action")


if __name__ == "__main__":
    unittest.main()
