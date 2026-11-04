from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nexus.common.claude_client import (
    ClaudeBudget,
    ClaudeClient,
    mask_secret,
    configured_model,
    configured_max_tokens,
)


class ClaudeBudgetTests(unittest.TestCase):
    def test_budget_persists_and_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "budget.json"
            budget = ClaudeBudget(path)
            self.assertTrue(budget.check(10).get("success"))
            snap = budget.record({"input_tokens": 7, "output_tokens": 3})
            self.assertEqual(snap["input_tokens"], 7)
            self.assertEqual(snap["output_tokens"], 3)
            self.assertEqual(snap["requests"], 1)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["input_tokens"], 7)

    def test_budget_limit_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "budget.json"
            budget = ClaudeBudget(path)
            with mock.patch.dict(os.environ, {"NEXUS_CLAUDE_DAILY_TOKEN_BUDGET": "10"}):
                budget.record({"input_tokens": 10})
                self.assertFalse(budget.check(1).get("success"))
                self.assertTrue(budget.check(0).get("success"))

    def test_zero_budget_means_unlimited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            budget = ClaudeBudget(Path(tmp) / "budget.json")
            with mock.patch.dict(os.environ, {"NEXUS_CLAUDE_DAILY_TOKEN_BUDGET": "0"}):
                budget.record({"input_tokens": 999999})
                self.assertTrue(budget.check(999999).get("success"))


class ClaudeClientTests(unittest.TestCase):
    def test_status_does_not_require_key(self) -> None:
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            out = ClaudeClient().status()
        self.assertFalse(out["configured"])
        self.assertEqual(out["provider"], "claude")

    def test_missing_key_returns_clean_error(self) -> None:
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            out = ClaudeClient().messages([{"role": "user", "content": "hi"}])
        self.assertFalse(out["success"])
        self.assertEqual(out["error"], "claude_not_configured")

    def test_mask_secret(self) -> None:
        text = "key sk-ant-abcdefghijklmnop and sk-ant-12345678"
        masked = mask_secret(text)
        self.assertNotIn("sk-ant-abcdefghijklmnop", masked)
        self.assertIn("sk-ant-***", masked)

    def test_env_limits_are_clamped(self) -> None:
        with mock.patch.dict(os.environ, {"NEXUS_CLAUDE_MAX_TOKENS": "999999",
                                          "NEXUS_CLAUDE_MODEL": "test-model"}):
            self.assertEqual(configured_model(), "test-model")
            self.assertLessEqual(configured_max_tokens(), 32000)


if __name__ == "__main__":
    unittest.main()
