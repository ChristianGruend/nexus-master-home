import tempfile, unittest
from pathlib import Path
from unittest import mock
import nexus.integrations.hermes_tools as ht


class HermesToolsSafety(unittest.TestCase):
    def setUp(self):
        self.wd = ht.WS / "hermes_tasks" / "_unittest"
        self.wd.mkdir(parents=True, exist_ok=True)

    def test_detects_text_toolcall(self):
        self.assertTrue(ht.has_text_toolcall('```python\nread_file(path="x.txt")\n```'))
        self.assertFalse(ht.has_text_toolcall("Der Code lautet NX-1."))

    def test_read_write_inside_workdir(self):
        ht.run_tool("write_file", {"path": "a.txt", "content": "hallo"}, self.wd)
        self.assertEqual(ht.run_tool("read_file", {"path": "a.txt"}, self.wd), "hallo")
        self.assertIn("a.txt", ht.run_tool("list_directory", {"path": "."}, self.wd))

    def test_blocks_outside_workspace(self):
        with self.assertRaises(PermissionError):
            ht.run_tool("read_file", {"path": r"C:\Windows\win.ini"}, self.wd)
        # Regel (Eddie 2026-11-04): nur workspace\ und nexus\ lesbar; Projektroot gesperrt
        with self.assertRaises(PermissionError):
            ht.run_tool("list_directory", {"path": str(ht.ROOT)}, self.wd)
        with self.assertRaises(PermissionError):
            ht.run_tool("read_file", {"path": str(ht.ROOT / "README.md")}, self.wd)
        self.assertIn("def ", ht.run_tool("read_file", {"path": "nexus/chat/server.py"}, self.wd))

    def test_write_only_in_workdir_and_no_secrets(self):
        with self.assertRaises(PermissionError):
            ht.run_tool("write_file", {"path": str(ht.WS / "x.txt"), "content": "x"}, self.wd)
        with self.assertRaises(PermissionError):
            ht.run_tool("read_file", {"path": "bridge_token.txt"}, self.wd)

    def test_native_loop_executes_tool_calls(self):
        (self.wd / "p.txt").write_text("NX-UT-1", encoding="utf-8")
        seq = [{"content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "p.txt"}}}]},
               {"content": "Code NX-UT-1"}]
        with mock.patch.object(ht, "_chat", side_effect=seq), mock.patch.object(ht, "_log"):
            r = ht.native_loop("lies p.txt", self.wd, "m", "HX-UT")
        self.assertEqual(r["reply"], "Code NX-UT-1")
        self.assertTrue(r["tool_calls"][0]["ok"])
        self.assertIn("NX-UT-1", r["tool_calls"][0]["result_preview"])


class HermesAgentRoutesTools(unittest.TestCase):
    def test_ask_uses_native_loop_for_tool_task(self):
        import nexus.integrations.hermes_agent as ha
        fake = {"reply": "Code X", "tool_calls": [{"tool": "read_file", "ok": True}], "turns": 2}
        with mock.patch.object(ht, "native_loop", return_value=fake), mock.patch.object(ha, "_log"), \
             mock.patch.object(ha.subprocess, "run", side_effect=AssertionError("CLI darf nicht laufen")):
            r = ha.ask("Lies mit read_file die Datei p.txt")
        self.assertTrue(r["success"])
        self.assertEqual(r["data"]["execution_status"], "HERMES_TOOLS_EXECUTED")
        self.assertEqual(r["data"]["tool_calls"][0]["tool"], "read_file")

class NativeLoopPartial(unittest.TestCase):
    def test_timeout_after_tools_keeps_calls(self):
        wd = ht.WS / "hermes_tasks" / "_unittest"; wd.mkdir(parents=True, exist_ok=True)
        (wd / "p.txt").write_text("NX-UT-2", encoding="utf-8")
        seq = [{"content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "p.txt"}}}]}, TimeoutError("timed out")]
        with mock.patch.object(ht, "_chat", side_effect=seq), mock.patch.object(ht, "_log"):
            r = ht.native_loop("lies", wd, "m", "HX-UT2")
        self.assertTrue(r["final_missing"])
        self.assertEqual(r["tool_calls"][0]["tool"], "read_file")

if __name__ == "__main__":
    unittest.main()