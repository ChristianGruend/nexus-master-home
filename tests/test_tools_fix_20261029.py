"""Regressionstests fuer die Werkzeug-/Latenz-Fixes vom 29.10.2026."""
import json
import unittest
from unittest import mock

from nexus.master import tools as T


class ParserFenceAndAlias(unittest.TestCase):
    def test_powershell_fence(self):
        calls = T.parse_tool_calls("Ich pruefe:\n```powershell\nGet-ChildItem workspace\n```", shell_fences=True)
        self.assertEqual(calls, [{"name": "powershell", "args": {"command": "Get-ChildItem workspace"}}])

    def test_ps1_and_shell_fence(self):
        self.assertEqual(T.parse_tool_calls("```ps1\nGet-Date\n```", shell_fences=True)[0]["name"], "powershell")
        self.assertEqual(T.parse_tool_calls("```shell\ndir\n```", shell_fences=True)[0]["name"], "shell")

    def test_shell_fences_off_by_default(self):
        self.assertEqual(T.parse_tool_calls("```powershell\nGet-Date\n```"), [])

    def test_duck_duck_go_alias_in_shell_fence(self):
        txt = '```shell\n{"name": "duck_duck_go_search", "args": {"q": "KI News"}}\n```'
        calls = T.parse_tool_calls(txt, shell_fences=True)
        self.assertEqual(calls, [{"name": "web_search", "args": {"q": "KI News", "query": "KI News"}}])

    def test_tool_attempt_detection(self):
        self.assertTrue(T.looks_like_tool_attempt("```powershell\nGet-Date\n```"))
        self.assertTrue(T.looks_like_tool_attempt("list_dir path=workspace"))
        self.assertTrue(T.looks_like_tool_attempt('{"name": "foo_tool", "args": {}}'))
        self.assertFalse(T.looks_like_tool_attempt("Hallo Eddie, alles gut."))


class _LLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, msgs, **kw):
        self.calls.append([dict(m) for m in msgs])
        return {"success": True, "reply": self.replies.pop(0), "model": "m"}


class _Master:
    def __init__(self, replies):
        self.llm = _LLM(replies)

    def build_context(self, session_messages, extra=""):
        return [{"role": "system", "content": "s"}]


def _run(replies, run_tools_result=None):
    from nexus.crew import runner as R
    m = _Master(replies)
    svc = R.CrewService.__new__(R.CrewService)
    fake_run = mock.Mock(side_effect=lambda calls, master: [
        {"name": c["name"], "args": c["args"], "result": run_tools_result or {"success": True, "reply": "ok"}} for c in calls])
    with mock.patch.object(R.CrewService, "_save_active"), \
            mock.patch("nexus.crew.direct.prepare", return_value=None), \
            mock.patch.object(R, "run_tools", fake_run):
        out = svc.run(m, "fixer", "pruefe den status bitte")
    return out, m, fake_run


class RunnerParseFailed(unittest.TestCase):
    def test_bad_format_twice_reports_tool_parse_failed(self):
        out, m, fake_run = _run(["Ich nutze `status` jetzt.\nstatus x=1", "system_monitor x=2"])
        self.assertEqual(out["data"]["execution_status"], "TOOL_PARSE_FAILED")
        self.assertEqual(out["error"], "TOOL_PARSE_FAILED")
        self.assertEqual(len(m.llm.calls), 2)  # genau ein Korrekturversuch
        self.assertIn("TOOL_PARSE_FAILED", m.llm.calls[1][-1]["content"])
        fake_run.assert_not_called()

    def test_correction_retry_executes_tool(self):
        out, m, fake_run = _run(["status x=1", '<tool_call>{"name":"status","args":{}}</tool_call>', "Fertig: Status ok."])
        self.assertEqual(out["data"]["execution_status"], "TOOLS_SUCCEEDED")
        self.assertEqual(out["data"]["tools"][0]["name"], "status")
        fake_run.assert_called_once()

    def test_plain_answer_stays_no_tools_required(self):
        out, m, _ = _run(["Alles ruhig, kein Werkzeug noetig."])
        self.assertEqual(out["data"]["execution_status"], "NO_TOOLS_REQUIRED")
        self.assertEqual(len(m.llm.calls), 1)


class SchreibeNotBuild(unittest.TestCase):
    def _src(self):
        import inspect
        from nexus.master import controller
        return inspect.getsource(controller)

    def test_schreibe_removed_from_plain_keyword_list(self):
        src = self._src()
        self.assertNotIn('"erstelle", "schreibe", "patch", "fix",', src)
        self.assertEqual(src.count('"erstelle", "patch", "fix",'), 2)

    def test_schreib_regex_needs_file_context(self):
        import re
        rx = (r"\bschreib\w*\b[\s\S]{0,80}?(?:datei|file|ordner|pfad|verzeichnis|skript|script|code|"
              r"\.(?:py|txt|md|json|ps1|js|html|css)\b|[a-z]:[\\/]|workspace)")
        self.assertIn(rx, self._src())
        self.assertIsNone(re.search(rx, "schreibe einen langen aufsatz ueber die geschichte roms"))
        self.assertIsNotNone(re.search(rx, "schreibe die datei workspace/x.txt"))


class TestSessionIsolated(unittest.TestCase):
    def test_e2e_session_does_not_touch_active_chat(self):
        from nexus.chat import engine as E
        eng = E.ChatEngine.__new__(E.ChatEngine) if hasattr(E, "ChatEngine") else None
        if eng is None:
            cls = [v for v in vars(E).values() if isinstance(v, type) and hasattr(v, "_prepare_session")][0]
            eng = cls.__new__(cls)
        import threading
        eng._save_lock = threading.RLock()
        eng.sessions = {"CHAT-A": {"id": "CHAT-A", "title": "Eddie", "messages": []}}
        eng.session_id = "CHAT-A"
        eng.master = mock.Mock()
        with mock.patch.object(type(eng), "_save_sessions"), mock.patch.object(type(eng), "_save_active") as sa:
            s = eng._prepare_session("Antworte nur mit dem Wort OK. NEXUS_E2E_1", "NEXUS-OS-E2E")
            s2 = eng._prepare_session("Antworte nur mit dem Wort OK. NEXUS_E2E_2", "NEXUS-OS-E2E")
        self.assertEqual(s["id"], "NEXUS-OS-E2E")
        self.assertIs(s, s2)
        self.assertEqual(eng.session_id, "CHAT-A")
        self.assertEqual(eng.sessions["CHAT-A"]["messages"], [])
        sa.assert_not_called()


class HermesNativeBody(unittest.TestCase):
    def test_body_has_num_ctx_and_keep_alive(self):
        import nexus.integrations.hermes_tools as ht
        with mock.patch("nexus.common.ai_layer.unified_num_ctx", return_value=8192), \
                mock.patch("nexus.common.ai_layer.keep_alive_value", return_value="30m"):
            body = ht._chat_body("qwen3.5:9b", [{"role": "user", "content": "x"}])
        self.assertEqual(body["options"]["num_ctx"], 8192)
        self.assertEqual(body["keep_alive"], "30m")
        self.assertTrue(body["tools"])
        json.dumps(body)


class InferKeepAlive(unittest.TestCase):
    def test_infer_no_longer_unloads(self):
        from nexus import nexus_os
        with mock.patch("nexus.common.ai_layer.keep_alive_value", return_value="30m"):
            self.assertEqual(nexus_os._infer_keep_alive(False), "30m")
        with mock.patch("nexus.common.ai_layer.keep_alive_value", return_value=0):
            self.assertEqual(nexus_os._infer_keep_alive(False), 0)
            self.assertEqual(nexus_os._infer_keep_alive(True), "10m")


class ScoutWebSearch(unittest.TestCase):
    def test_scout_may_web_search(self):
        from nexus.crew.capabilities import allowed_tools_for, filter_tool_calls
        self.assertIn("web_search", allowed_tools_for("scout"))
        ok, denied = filter_tool_calls("scout", [{"name": "web_search", "args": {"query": "x"}},
                                                 {"name": "powershell", "args": {"command": "x"}}])
        self.assertEqual([c["name"] for c in ok], ["web_search"])
        self.assertEqual(denied, ["powershell"])


class ConfigKeepAlive(unittest.TestCase):
    def test_ai_models_keep_alive_30m(self):
        from nexus.common.paths import WORKSPACE
        cfg = json.loads((WORKSPACE / "apex" / "ai_models.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["keep_alive"], "30m")
        self.assertEqual(cfg["num_ctx"], 8192)
