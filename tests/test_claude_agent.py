from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nexus.integrations import claude_agent


class FakeClient:
    api_key = "test-key"
    model = "fake-claude"

    def __init__(self) -> None:
        self.calls = 0

    def status(self) -> dict:
        return {"provider": "claude", "configured": True, "model": self.model,
                "budget": {"used_tokens": 0, "limit": 1000}}

    def messages(self, messages, *, system=None, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "success": True,
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "mouse_click",
                             "input": {"x": 10, "y": 20}}],
                "text": "",
                "model": self.model,
                "usage": {"input_tokens": 3, "output_tokens": 4},
                "stop_reason": "tool_use",
            }
        return {
            "success": True,
            "content": [{"type": "text", "text": "fertig"}],
            "text": "fertig",
            "model": self.model,
            "usage": {"input_tokens": 2, "output_tokens": 1},
            "stop_reason": "end_turn",
        }


class ApprovalRetryTests(unittest.TestCase):
    def test_resume_re_executes_pending_tool(self) -> None:
        calls: list[dict] = []

        def fake_run_one(call, master, agent="NEXUS"):
            calls.append(call)
            if len(calls) == 1:
                return {"success": False, "status": "APPROVAL_REQUIRED",
                        "error": "desktop_approval_required", "approval_id": "appr_1"}
            return {"success": True, "action": "mouse_click", "verified": False}

        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            with mock.patch.object(claude_agent, "TASK_DIR", Path(tmp)), \
                    mock.patch.object(claude_agent, "run_one", side_effect=fake_run_one):
                agent = claude_agent.ClaudeAgent(master=object(), client=client)
                first = agent.run("klicke")
                self.assertEqual(first["status"], "APPROVAL_REQUIRED")
                self.assertEqual(first["approval_id"], "appr_1")
                task_id = first["task_id"]

                second = agent.resume(task_id, "appr_1")
                self.assertTrue(second["success"])
                self.assertEqual(second["status"], "DONE")
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[1]["args"].get("approval_id"), "appr_1")
                self.assertEqual(client.calls, 2)

    def test_status_and_capabilities_are_safe_without_master(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(claude_agent, "TASK_DIR", Path(tmp)):
                agent = claude_agent.ClaudeAgent(master=object(), client=FakeClient())
                status = agent.status()
                caps = agent.capabilities()
        self.assertTrue(status["success"])
        self.assertTrue(status["configured"])
        self.assertIn("shell", caps["tools"])
        self.assertEqual(caps["provider"], "claude")


if __name__ == "__main__":
    unittest.main()
