"""Phase-1 Wahrheitstests fuer den NEXUS Master-Agent.

Offline: Fake-LLM, echte Tools (write_file/read_file/PowerShell).
Prueft die verbindlichen Regeln:
  EXECUTED != VERIFIED, Exitcode != PASS, fehlende Verifikation != VERIFIED,
  Modellfehler zerstoert einen laufenden Auftrag nicht,
  idempotente Tool-Wiederholung, Checkpoint/Resume.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import nexus.master.controller as ctrl_mod
from nexus.master.controller import MasterController


def _tool_reply(name: str, args: dict) -> str:
    return (
        "<tool_call>"
        + json.dumps({"name": name, "args": args}, ensure_ascii=False)
        + "</tool_call>"
    )


class FakeLLM:
    """Scripted Ersatz fuer OllamaClient.chat."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        if not self.script:
            return {"success": True, "reply": "Fertig.", "model": "fake"}
        step = self.script[0]
        if isinstance(step, dict) and step.get("__fail__"):
            self.script.pop(0)
            # __sticky__: Fehler bleibt ueber alle Retry-Versuche bestehen.
            if step.get("__sticky__"):
                self.script.insert(0, step)
            return {
                "success": False,
                "reply": "",
                "error": str(step["__fail__"]),
                "model": None,
            }
        self.script.pop(0)
        return {"success": True, "reply": str(step), "model": "fake"}


class Phase1Base(unittest.TestCase):
    def setUp(self) -> None:
        self.c = MasterController()
        self.tmp = Path(tempfile.mkdtemp(prefix="nexus_phase1_"))
        self.state_path = self.tmp / "nexus_active_task.json"
        self.c._task_state_path = lambda: self.state_path  # type: ignore[method-assign]
        self.c._act = lambda *a, **k: None  # type: ignore[method-assign]
        self.exec_counts: dict[str, int] = {}
        self._orig_run_tools = ctrl_mod.run_tools
        _orig = self._orig_run_tools
        owner = self

        def counting(calls, master, _orig=_orig):
            result = _orig(calls, master)
            for item in result:
                if isinstance(item, dict):
                    name = str(item.get("name") or "")
                    owner.exec_counts[name] = owner.exec_counts.get(name, 0) + 1
            return result

        ctrl_mod.run_tools = counting  # type: ignore[assignment]
        self.files_to_remove: list[Path] = []

    def tearDown(self) -> None:
        ctrl_mod.run_tools = self._orig_run_tools  # type: ignore[assignment]
        for p in self.files_to_remove:
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_stream(self, message: str, script: list) -> dict:
        self.c.llm = FakeLLM(script)
        with patch("time.sleep"):
            events = list(self.c.handle_stream(message, []))
        done = [e for e in events if isinstance(e, dict) and e.get("type") == "done"]
        self.assertTrue(done, f"kein done-Event fuer: {message[:60]}")
        return done[-1]

    def read_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))


class Test1Write(Phase1Base):
    def test_write_is_really_executed(self):
        rel = "workspace/phase1_t1.txt"
        target = ROOT / rel
        self.files_to_remove.append(target)
        if target.exists():
            target.unlink()

        msg = (
            "Schreibe die Datei workspace/phase1_t1.txt "
            "mit dem Inhalt PHASE1_WRITE_OK."
        )
        done = self.run_stream(
            msg,
            [
                _tool_reply(
                    "write_file",
                    {"path": rel, "content": "PHASE1_WRITE_OK"},
                ),
                "Erledigt.",
            ],
        )

        self.assertTrue(target.is_file(), "Datei wurde nicht real geschrieben")
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "PHASE1_WRITE_OK",
        )
        self.assertTrue(done.get("success"), done.get("error"))
        self.assertEqual(self.exec_counts.get("write_file"), 1)
        self.assertEqual(self.read_state().get("status"), "PASS")


class Test2Readback(Phase1Base):
    def test_verification_read_after_write(self):
        rel = "workspace/phase1_t2.txt"
        target = ROOT / rel
        self.files_to_remove.append(target)
        if target.exists():
            target.unlink()

        msg = (
            "Schreibe die Datei workspace/phase1_t2.txt mit dem Inhalt "
            "PHASE1_VERIFY_OK und verifiziere den Inhalt danach mit read_file."
        )
        done = self.run_stream(
            msg,
            [
                _tool_reply(
                    "write_file",
                    {"path": rel, "content": "PHASE1_VERIFY_OK"},
                ),
                _tool_reply("read_file", {"path": rel}),
                "Verifiziert.",
            ],
        )

        names = [
            str(t.get("name"))
            for t in (done.get("data") or {}).get("tools", [])
        ]
        self.assertIn("write_file", names)
        self.assertIn("read_file", names)
        self.assertLess(
            names.index("write_file"),
            names.index("read_file"),
            "read_file lief nicht nach dem write_file",
        )
        self.assertTrue(done.get("success"), done.get("error"))


class Test3Idempotenz(Phase1Base):
    def test_identical_tool_call_runs_once(self):
        rel = "workspace/phase1_t3.txt"
        target = ROOT / rel
        self.files_to_remove.append(target)
        if target.exists():
            target.unlink()

        msg = (
            "Schreibe die Datei workspace/phase1_t3.txt "
            "mit dem Inhalt PHASE1_IDEMPOTENT_OK."
        )
        same_call = _tool_reply(
            "write_file",
            {"path": rel, "content": "PHASE1_IDEMPOTENT_OK"},
        )
        done = self.run_stream(msg, [same_call, same_call, "Fertig."])

        self.assertEqual(
            self.exec_counts.get("write_file"),
            1,
            "identischer Tool-Aufruf wurde mehrfach ausgefuehrt",
        )
        tools = (done.get("data") or {}).get("tools", [])
        writes = [t for t in tools if str(t.get("name")) == "write_file"]
        self.assertEqual(len(writes), 1)
        self.assertTrue(done.get("success"), done.get("error"))


class Test4PowerShellSuccess(Phase1Base):
    def test_free_powershell_is_blocked_not_executed(self):
        msg = (
            "Fuehre diesen PowerShell-Befehl wirklich aus:\n"
            "Write-Output PHASE1_PS_OK\n"
            "exit 0"
        )
        done = self.run_stream(msg, [])
        data = done.get("data") or {}

        # Keine freie Shell-Ausfuehrung: nur registrierte Aktionen mit Freigabe.
        self.assertFalse(done.get("success"))
        self.assertEqual(done.get("error"), "REGISTERED_ACTION_REQUIRED")
        self.assertEqual(data.get("execution_status"), "INCOMPLETE")
        self.assertNotEqual(data.get("execution_status"), "TOOLS_SUCCEEDED")
        self.assertNotIn("PASS", (done.get("reply") or "").upper())
        self.assertEqual(self.read_state().get("status"), "FAILED")


class Test5PowerShellFailure(Phase1Base):
    def test_nonzero_exit_never_reports_pass(self):
        msg = (
            "Fuehre diesen PowerShell-Befehl wirklich aus:\n"
            "Write-Output PHASE1_PS_FAIL\n"
            "exit 7"
        )
        done = self.run_stream(msg, [])
        data = done.get("data") or {}

        self.assertFalse(done.get("success"), "Exitcode 7 meldete Erfolg")
        self.assertEqual(done.get("error"), "REGISTERED_ACTION_REQUIRED")
        self.assertNotEqual(data.get("execution_status"), "TOOLS_SUCCEEDED")
        self.assertNotIn("PASS", (done.get("reply") or "").upper())
        self.assertNotEqual(self.read_state().get("status"), "PASS")


class Test6ModelRetry(Phase1Base):
    def test_retryable_error_is_retried_bounded(self):
        self.c.llm = FakeLLM(
            [
                {"__fail__": "HTTP 429: rate limit exceeded"},
                {"__fail__": "HTTP 503: service unavailable"},
                {"success": True, "reply": "geht wieder"},
            ]
        )
        with patch("time.sleep"):
            out, attempts = self.c._chat_with_retry([])
        self.assertTrue(out.get("success"))
        self.assertEqual(attempts, 3)

    def test_non_retryable_error_stops_immediately(self):
        self.c.llm = FakeLLM([{"__fail__": "no_model"}])
        with patch("time.sleep"):
            out, attempts = self.c._chat_with_retry([])
        self.assertFalse(out.get("success"))
        self.assertEqual(attempts, 1, "nicht-retrybarer Fehler wurde wiederholt")

    def test_retry_classifier(self):
        self.assertTrue(MasterController._model_error_retryable("HTTP 429: x"))
        self.assertTrue(MasterController._model_error_retryable("ollama_down"))
        self.assertTrue(MasterController._model_error_retryable("timed out"))
        self.assertTrue(MasterController._model_error_retryable("HTTP 500: x"))
        self.assertFalse(MasterController._model_error_retryable("no_model"))
        self.assertFalse(
            MasterController._model_error_retryable("model not found")
        )
        self.assertFalse(MasterController._model_error_retryable(""))


class Test7ModelFailureAfterTools(Phase1Base):
    def test_partial_checkpoint_and_resume(self):
        rel = "workspace/phase1_t7.txt"
        target = ROOT / rel
        self.files_to_remove.append(target)
        if target.exists():
            target.unlink()

        msg = (
            "Schreibe die Datei workspace/phase1_t7.txt mit dem Inhalt "
            "PHASE1_RESUME_OK und verifiziere den Inhalt danach mit read_file."
        )

        # Lauf 1: Write gelingt, Modell faellt danach aus.
        done = self.run_stream(
            msg,
            [
                _tool_reply(
                    "write_file",
                    {"path": rel, "content": "PHASE1_RESUME_OK"},
                ),
                {"__fail__": "HTTP 500: internal error", "__sticky__": True},
            ],
        )

        self.assertFalse(done.get("success"))
        self.assertEqual(done.get("error"), "llm_failed_after_tools")
        self.assertIn("BLOCKED", done.get("reply") or "")
        self.assertIn("weiter", done.get("reply") or "")
        self.assertEqual(self.exec_counts.get("write_file"), 1)

        state = self.read_state()
        self.assertEqual(state.get("status"), "PARTIAL")
        self.assertTrue(state.get("run_id"))
        self.assertEqual(state.get("checkpoint"), "final")
        self.assertEqual(state.get("error"), "llm_failed_after_tools")
        self.assertEqual(len(state.get("tools") or []), 1)
        self.assertTrue((state.get("tools") or [{}])[0].get("ok"))
        self.assertTrue(target.is_file(), "Write ging verloren")

        # Lauf 2 (Resume): identischer Auftrag, Write darf nicht laufen.
        done2 = self.run_stream(
            msg,
            [
                _tool_reply(
                    "write_file",
                    {"path": rel, "content": "PHASE1_RESUME_OK"},
                ),
                _tool_reply("read_file", {"path": rel}),
                "Verifiziert.",
            ],
        )

        self.assertEqual(
            self.exec_counts.get("write_file"),
            1,
            "Resume hat den bereits ausgefuehrten Write wiederholt",
        )
        self.assertEqual(self.exec_counts.get("read_file"), 1)
        self.assertTrue(done2.get("success"), done2.get("error"))

        tools = (done2.get("data") or {}).get("tools", [])
        restored = [t for t in tools if t.get("restored")]
        self.assertTrue(restored, "kein wiederhergestellter Nachweis im Trace")
        self.assertEqual(self.read_state().get("status"), "PASS")

    def test_model_failure_with_incomplete_goal_stays_blocked(self):
        rel = "workspace/phase1_t7b.txt"
        target = ROOT / rel
        self.files_to_remove.append(target)
        if target.exists():
            target.unlink()

        msg = (
            "Schreibe die Datei workspace/phase1_t7b.txt mit dem Inhalt "
            "PHASE1_BLOCKED_OK und verifiziere den Inhalt danach mit read_file."
        )
        done = self.run_stream(
            msg,
            [
                _tool_reply(
                    "write_file",
                    {"path": rel, "content": "PHASE1_BLOCKED_OK"},
                ),
                {"__fail__": "ollama_down", "__sticky__": True},
            ],
        )

        reply = done.get("reply") or ""
        self.assertFalse(done.get("success"))
        self.assertEqual(done.get("error"), "llm_failed_after_tools")
        self.assertIn("BLOCKED", reply)
        self.assertNotIn("Modellaufruf fehlgeschlagen; Auftrag", reply)
        self.assertNotEqual(self.read_state().get("status"), "PASS")


class Test8FakePassGuard(Phase1Base):
    def test_single_action_batch_still_runs_goal_verification(self):
        """Ein erfolgreicher Write-Batch darf einen unvollstaendigen
        Mehrschrittauftrag nicht als PASS abschliessen."""
        rel = "workspace/phase1_t8.txt"
        target = ROOT / rel
        self.files_to_remove.append(target)
        if target.exists():
            target.unlink()

        msg = (
            "Schreibe die Datei workspace/phase1_t8.txt mit dem Inhalt "
            "PHASE1_GUARD_OK und verifiziere den Inhalt danach mit read_file. "
            "Lies die Datei danach mit read_file."
        )
        done = self.run_stream(
            msg,
            [
                _tool_reply(
                    "write_file",
                    {"path": rel, "content": "PHASE1_GUARD_OK"},
                ),
                "WRITE_FILE PASS",
            ],
        )

        reply = done.get("reply") or ""
        self.assertFalse(
            done.get("success"),
            f"unvollstaendiger Auftrag meldete Erfolg: {reply[:200]}",
        )
        self.assertNotEqual(self.read_state().get("status"), "PASS")


if __name__ == "__main__":
    unittest.main()
