"""Offline contracts: backup-vor-Änderung (Repair-Sicherung) und begrenzte Patch-Fähigkeit.

No network, no bridge, no LLM. The bridge executors are exercised against a relocated
ALLOWED_ROOT so the real workspace stays untouched; the Hermes tool contract is validated
statically.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nexus.bridge import executors as ex
from nexus.integrations.hermes_client import IntegrationError, TASKS, validate_task

CODE_FILE = "workspace/projects/hermes/code/rechner/rechner.py"


class BackupBeforeWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.backup = self.root / "workspace" / "backups"
        patchers = (
            mock.patch.object(ex, "ALLOWED_ROOT", self.root),
            mock.patch.object(ex, "WORKSPACE", self.root / "workspace"),
            mock.patch.object(ex, "BACKUP_DIR", self.backup),
        )
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_write_file_backs_up_existing(self):
        target = self.root / "projekt" / "datei.py"
        target.parent.mkdir(parents=True)
        target.write_text("alpha\n", encoding="utf-8")
        result = ex.exec_write_file({"path": "projekt/datei.py", "content": "beta\n"}, None)
        self.assertTrue(result["atomic"])
        self.assertTrue(result["backup_snapshot"])
        self.assertEqual(target.read_text(encoding="utf-8"), "beta\n")
        backup = self.root / result["backup_snapshot"]
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_text(encoding="utf-8"), "alpha\n")

    def test_patch_file_backs_up_and_changes(self):
        target = self.root / "projekt" / "datei.py"
        target.parent.mkdir(parents=True)
        target.write_text("beta\n", encoding="utf-8")
        result = ex.exec_patch_file(
            {"path": "projekt/datei.py", "old_text": "beta", "new_text": "gamma"}, None)
        self.assertTrue(result["changed"])
        self.assertEqual(result["replacements"], 1)
        self.assertTrue(result["backup_snapshot"])
        self.assertEqual(target.read_text(encoding="utf-8"), "gamma\n")

    def test_patch_file_no_match_changes_nothing(self):
        target = self.root / "projekt" / "datei.py"
        target.parent.mkdir(parents=True)
        target.write_text("x\n", encoding="utf-8")
        result = ex.exec_patch_file(
            {"path": "projekt/datei.py", "old_text": "unbekannt", "new_text": "y"}, None)
        self.assertFalse(result["changed"])
        self.assertEqual(result["replacements"], 0)
        self.assertEqual(target.read_text(encoding="utf-8"), "x\n")

    def test_delete_file_backs_up(self):
        target = self.root / "projekt" / "datei.py"
        target.parent.mkdir(parents=True)
        target.write_text("alpha\n", encoding="utf-8")
        result = ex.exec_delete_file({"path": "projekt/datei.py", "confirm": True}, None)
        self.assertTrue(result["deleted"])
        self.assertTrue(result["backup_snapshot"])
        self.assertFalse(target.exists())
        self.assertTrue((self.root / result["backup_snapshot"]).is_file())


class BackupFailureAbortsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.backup = self.root / "workspace" / "backups"
        patchers = (
            mock.patch.object(ex, "ALLOWED_ROOT", self.root),
            mock.patch.object(ex, "WORKSPACE", self.root / "workspace"),
            mock.patch.object(ex, "BACKUP_DIR", self.backup),
        )
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _target(self, content="alpha\n"):
        target = self.root / "projekt" / "datei.py"
        target.parent.mkdir(parents=True)
        target.write_text(content, encoding="utf-8")
        return target

    def test_write_file_aborts_on_backup_failure(self):
        target = self._target()
        with mock.patch.object(ex.shutil, "copy2", side_effect=OSError("media full")):
            with self.assertRaises(ex.BridgeError) as ctx:
                ex.exec_write_file({"path": "projekt/datei.py", "content": "beta\n"}, None)
        self.assertIn("aborted", str(ctx.exception))
        self.assertEqual(target.read_text(encoding="utf-8"), "alpha\n")

    def test_patch_file_aborts_on_backup_failure(self):
        target = self._target("beta\n")
        with mock.patch.object(ex.shutil, "copy2", side_effect=OSError("media full")):
            with self.assertRaises(ex.BridgeError):
                ex.exec_patch_file({"path": "projekt/datei.py", "old_text": "beta", "new_text": "gamma"}, None)
        self.assertEqual(target.read_text(encoding="utf-8"), "beta\n")

    def test_delete_file_aborts_on_backup_failure(self):
        target = self._target()
        with mock.patch.object(ex.shutil, "copy2", side_effect=OSError("media full")):
            with self.assertRaises(ex.BridgeError):
                ex.exec_delete_file({"path": "projekt/datei.py", "confirm": True}, None)
        self.assertTrue(target.exists())


class HermesPatchContractTest(unittest.TestCase):
    def test_patch_file_in_contract(self):
        self.assertIn("patch_file", TASKS)

    def test_patch_file_accepts_released_code_path(self):
        validate_task("patch_file", {"path": CODE_FILE, "old_text": "a", "new_text": "b"})

    def test_patch_file_rejects_bad_params(self):
        with self.assertRaises(IntegrationError):
            validate_task("patch_file", {"path": CODE_FILE, "old_text": "a"})
        with self.assertRaises(IntegrationError):
            validate_task("patch_file", {"path": CODE_FILE, "old_text": "", "new_text": "b"})
        with self.assertRaises(IntegrationError):
            validate_task("patch_file", {"path": CODE_FILE, "old_text": "a", "new_text": "a"})
        with self.assertRaises(IntegrationError):
            validate_task("patch_file", {"path": "docs/foo.py", "old_text": "a", "new_text": "b"})


if __name__ == "__main__":
    unittest.main()