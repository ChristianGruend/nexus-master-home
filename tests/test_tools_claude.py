from __future__ import annotations

import unittest
from unittest import mock

from nexus.master import tools


class FakeShell:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, command, **kwargs):
        self.calls.append({"command": command, **kwargs})
        return {"success": True, "status": "OK", "stdout": "ok"}

    def run_script(self, path, **kwargs):
        self.calls.append({"path": path, **kwargs})
        return {"success": True, "status": "OK", "stdout": "ok"}


class FakeMaster:
    def __init__(self) -> None:
        self.shell = FakeShell()


class ClaudeToolDispatchTests(unittest.TestCase):
    def test_shell_receives_agent_and_approval(self) -> None:
        master = FakeMaster()
        out = tools.run_one({"name": "shell", "args": {"command": "echo ok",
                                                       "approval_id": "apr_1"}},
                            master, agent="Claude")
        self.assertTrue(out["success"])
        self.assertEqual(master.shell.calls[0]["agent"], "Claude")
        self.assertEqual(master.shell.calls[0]["approval_id"], "apr_1")

    def test_run_script_receives_agent(self) -> None:
        master = FakeMaster()
        out = tools.run_one({"name": "run_script", "args": {"path": "x.py",
                                                            "args": ["a"]}},
                            master, agent="Claude")
        self.assertTrue(out["success"])
        self.assertEqual(master.shell.calls[0]["agent"], "Claude")

    def test_screen_read_uses_vision(self) -> None:
        class LLM:
            def vision(self, prompt, image, **kwargs):
                return {"success": True, "reply": "sichtbar: Editor"}

        master = FakeMaster()
        master.llm = LLM()
        with mock.patch("nexus.computer.desktop.screenshot",
                        return_value={"success": True, "path": __file__}):
            out = tools.run_one({"name": "screen_read", "args": {"prompt": "was ist sichtbar?"}},
                                master, agent="Claude")
        self.assertTrue(out["success"])
        self.assertEqual(out["action"], "screen_read")
        self.assertIn("Editor", out["reply"])

    def test_desktop_tool_receives_approval(self) -> None:
        with mock.patch("nexus.computer.desktop.mouse_click",
                        return_value={"success": False, "status": "APPROVAL_REQUIRED"}) as click:
            out = tools.run_one({"name": "mouse_click",
                                 "args": {"x": 1, "y": 2, "approval_id": "apr_2"}},
                                FakeMaster(), agent="Claude")
        self.assertEqual(out["status"], "APPROVAL_REQUIRED")
        self.assertEqual(click.call_args.kwargs["approval_id"], "apr_2")


if __name__ == "__main__":
    unittest.main()
