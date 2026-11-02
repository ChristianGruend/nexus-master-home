"""NEXUS_LEGACY_STATE_TRUST_V1.

Ein gespeicherter Task-Zustand mit `provenance: legacy_tool_summary_unverified`
oder `resume_allowed: false` darf weder Zielnachweis noch Erfolgsbeleg sein.

A/B: gleicher Zustand, gleiche Nachricht, gleicher Modellverlauf
  - quarantaniert  -> kein PASS, kein Erfolg
  - ohne Marker    -> Resume erkennt die Nachweise (Referenzverhalten)
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

from nexus.master.controller import MasterController

MESSAGE = (
    "Schreibe die Datei workspace/p5_auftrag.txt mit dem Inhalt "
    "P5_OK und verifiziere den Inhalt danach mit read_file."
)


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        if not self.script:
            return {"success": True, "reply": "Fertig.", "model": "fake"}
        step = self.script.pop(0)
        if isinstance(step, dict) and step.get("__fail__"):
            return {
                "success": False,
                "reply": "",
                "error": str(step["__fail__"]),
                "model": None,
            }
        return {"success": True, "reply": str(step), "model": "fake"}


def _quarantined_state() -> dict:
    return {
        "status": "BLOCKED",
        "intent": "execute",
        "task": MESSAGE,
        "tools": [
            {"name": "write_file", "path": "workspace/p5_auftrag.txt", "ok": True},
            {"name": "read_file", "path": "workspace/p5_auftrag.txt", "ok": True},
        ],
        "error": "LEGACY_TASK_HAS_NO_REGISTERED_RECEIPTS",
        "result": "Keine automatische Wiederholung.",
        "resume_allowed": False,
        "provenance": "legacy_tool_summary_unverified",
        "updated_at": "2026-10-09T12:30:46+00:00",
    }


class LegacyStateTrustBase(unittest.TestCase):
    def setUp(self) -> None:
        self.c = MasterController()
        self.tmp = Path(tempfile.mkdtemp(prefix="nexus_p5_"))
        self.state_path = self.tmp / "nexus_active_task.json"
        self.c._task_state_path = lambda: self.state_path  # type: ignore[method-assign]
        self.c._act = lambda *a, **k: None  # type: ignore[method-assign]

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed_state(self, state: dict) -> None:
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    def read_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def run_stream(self, script: list) -> dict:
        self.c.llm = FakeLLM(script)
        with patch("time.sleep"):
            events = list(self.c.handle_stream(MESSAGE, []))
        done = [e for e in events if isinstance(e, dict) and e.get("type") == "done"]
        self.assertTrue(done, "kein done-Event")
        return done[-1]


class TestQuarantinedState(LegacyStateTrustBase):
    def test_untrusted_state_never_becomes_goal_evidence(self):
        self.seed_state(_quarantined_state())

        done = self.run_stream(["Erledigt."])

        self.assertFalse(
            done.get("success"),
            f"quarantanierter Legacy-Zustand meldete Erfolg: {done.get('reply')}",
        )
        self.assertNotEqual(self.read_state().get("status"), "PASS")
        self.assertIn("BLOCKED", str(done.get("reply") or "").upper())

    def test_untrusted_state_is_not_persisted_as_new_evidence(self):
        self.seed_state(_quarantined_state())

        self.run_stream(["Erledigt."])

        tools = self.read_state().get("tools") or []
        self.assertFalse(
            any(t.get("name") == "write_file" and t.get("ok") for t in tools),
            f"ungepruefte Legacy-Zusammenfassung wurde weitergereicht: {tools}",
        )


class TestTrustedStateControl(LegacyStateTrustBase):
    def test_state_without_markers_still_resumes(self):
        state = _quarantined_state()
        state.pop("resume_allowed")
        state.pop("provenance")
        state.pop("error")
        self.seed_state(state)

        done = self.run_stream(["Erledigt."])

        self.assertTrue(
            done.get("success"),
            f"Referenzverhalten (vertrauenswuerdiger Zustand) brach ab: "
            f"{done.get('error')} {str(done.get('reply'))[:300]}",
        )
        self.assertEqual(self.read_state().get("status"), "PASS")


if __name__ == "__main__":
    unittest.main()
