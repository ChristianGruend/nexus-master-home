"""D9: Self-Improve darf keine ungepruefte Aenderung als verified melden."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import MagicMock

import nexus.memory.self_improve as si


def _fresh_paths():
    base = Path(mkdtemp(prefix="si_vrf_"))
    apex = base / "apex"
    root = base / "root"
    apex.mkdir()
    root.mkdir()
    return base, apex, root


class SelfImproveVerified(unittest.TestCase):
    """Testet die Self-Improve-Pfade isoliert in einem Temp-Baum."""

    def setUp(self):
        self.base, self.apex, self.root = _fresh_paths()
        patches = [
            ("APEX", self.apex),
            ("ROOT", self.root),
            ("QUEUE", self.apex / "SELF_IMPROVE_QUEUE.md"),
            ("STAMP", self.apex / "SELF_IMPROVE_LAST.json"),
            (
                "PROMPT",
                self.apex / "NEXUS_SELBSTVERBESSERUNG_PROMPT.md",
            ),
            (
                "PROMPT_ROOT",
                self.root / "NEXUS_SELBSTVERBESSERUNG_PROMPT.md",
            ),
            ("KNOWLEDGE", self.apex / "PROJECT_KNOWLEDGE.md"),
            ("CORRECTIONS", self.apex / "eddie_corrections.json"),
            (
                "ARBEITSPLAN",
                self.root / "NEXUS_SELBSTENTWICKLUNG_ARBEITSPLAN.md",
            ),
            (
                "ARBEITSPLAN_APEX",
                self.apex / "NEXUS_SELBSTENTWICKLUNG_ARBEITSPLAN.md",
            ),
            ("LOGS", self.root / "logs"),
        ]
        self._saved = []
        for name, value in patches:
            self._saved.append((name, getattr(si, name)))
            setattr(si, name, value)
        self.root.joinpath("logs").mkdir()

    def tearDown(self):
        for name, original in self._saved:
            setattr(si, name, original)

    def _write_corrections(self, count: int = 2):
        items = [
            {
                "id": f"c{i}",
                "kind": "always",
                "safe": True,
                "text": f"Regel {i} (test)",
            }
            for i in range(count)
        ]
        si.CORRECTIONS.write_text(
            json.dumps({"items": items}, ensure_ascii=False),
            encoding="utf-8",
        )

    def run_loop(self, apply_safe: bool) -> dict:
        return si.SelfImproveLoop().run(
            trigger="manual",
            apply_safe=apply_safe,
        )

    def test_analysis_mode_no_fake_verified(self):
        self._write_corrections()
        r = self.run_loop(apply_safe=False)
        self.assertTrue(r.get("success"))
        self.assertFalse(r.get("result", {}).get("verified"))

    def test_apply_sync_is_read_back_verified(self):
        self._write_corrections()
        si.PROMPT_ROOT.write_text("logik", encoding="utf-8")
        r = self.run_loop(apply_safe=True)
        self.assertTrue(r.get("success"), r.get("error"))
        self.assertTrue(r.get("result", {}).get("verified"))
        self.assertTrue(
            r.get("result", {}).get("verified_changes"),
            "nur Read-back-bestaetigte Syncs zaehlen",
        )
        self.assertEqual(
            si.PROMPT.read_text(encoding="utf-8"),
            "logik",
        )

    def test_already_in_sync_no_verified_pass(self):
        self._write_corrections()
        si.PROMPT_ROOT.write_text("gleich", encoding="utf-8")
        si.PROMPT.write_text("gleich", encoding="utf-8")
        r = self.run_loop(apply_safe=True)
        self.assertFalse(r.get("success"))
        self.assertEqual(r.get("error"), "no_verified_improvement")
        self.assertFalse(r.get("result", {}).get("verified"))

    def test_read_back_mismatch_is_failure(self):
        self._write_corrections(0)
        fake = MagicMock()
        fake.is_file.return_value = True
        fake.read_text.return_value = "anderer-inhalt"
        si.PROMPT = fake
        si.PROMPT_ROOT.write_text("original", encoding="utf-8")
        r = self.run_loop(apply_safe=True)
        self.assertFalse(r.get("success"))
        self.assertEqual(r.get("error"), "safe_apply_partial_failure")
        self.assertTrue(r.get("result", {}).get("apply_failures"))


if __name__ == "__main__":
    unittest.main()