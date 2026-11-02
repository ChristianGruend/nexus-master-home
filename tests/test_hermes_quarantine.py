"""Offline quarantine safety checks; every fixture and log is isolated on G:."""
import builtins
import ctypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nexus.integrations import hermes_quarantine as quarantine
from nexus.integrations import hermes_runas as runas


class QuarantineSafetyTests(unittest.TestCase):
    def setUp(self):
        cache = Path(__file__).resolve().parents[1] / "workspace/cache"
        cache.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="quarantine_offline_", dir=cache)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixtures = self.root / "fixtures"
        self.fixtures.mkdir()
        self.log = self.root / "workspace/hermes_service/quarantine.log"
        self.qroot = self.root / "workspace/hermes_quarantine"
        context = patch.multiple(quarantine, ROOT=self.root, QLOG=self.log, QROOT=self.qroot)
        context.start()
        self.addCleanup(context.stop)

    def fixture(self, name, content="foreign data"):
        path = self.fixtures / name
        path.write_text(content, encoding="utf-8")
        os.utime(path, ns=(1700000000000000000, 1700000000000000000))
        return path

    def snapshot(self, path):
        info = path.stat()
        return (info.st_ino, info.st_size, info.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest())

    def test_same_timestamp_test_names_and_claimed_request_never_authorize_move(self):
        paths = [self.fixture(name) for name in
                 ["ordinary.txt", "qa_foreign.txt", "decoy_other_job.txt", "other_hermes_test.txt"]]
        before = {path: self.snapshot(path) for path in paths}
        with patch("shutil.move", side_effect=AssertionError("must not move")), \
                patch.object(Path, "rename", side_effect=AssertionError("must not rename")), \
                patch.object(Path, "unlink", side_effect=AssertionError("must not delete")):
            result = quarantine.quarantine(paths, "HX-claimed", "verified_creation_claim")
        self.assertEqual(result["moved"], [])
        self.assertIsNone(result["quarantine_dir"])
        self.assertEqual(result["failed"], [])
        self.assertEqual(set(result["reported"]), {str(path) for path in paths})
        self.assertFalse(self.qroot.exists())
        self.assertEqual(before, {path: self.snapshot(path) for path in paths})
        records = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(x["origin"] == "unverified" and x["action"] == "report_only" for x in records))

    def test_candidate_content_is_not_opened_or_hashed(self):
        path = self.fixture("qa_private_content.txt")
        real_open = builtins.open

        def guarded_open(name, *args, **kwargs):
            if os.fspath(name) == str(path):
                raise AssertionError("quarantine must not open unproven source")
            return real_open(name, *args, **kwargs)

        with patch("builtins.open", side_effect=guarded_open):
            result = quarantine.quarantine([path], "job", "snapshot")
        self.assertEqual(result["reported"], [str(path)])

    def test_sensitive_file_is_skipped_without_content_in_log(self):
        path = self.fixture(".env", "PRIVATE_FIXTURE_DO_NOT_LOG")
        before = self.snapshot(path)
        result = quarantine.quarantine([path], "job", "snapshot")
        self.assertEqual(result["skipped"], [str(path)])
        self.assertEqual(result["reported"], [])
        self.assertEqual(self.snapshot(path), before)
        self.assertNotIn("PRIVATE_FIXTURE_DO_NOT_LOG", self.log.read_text(encoding="utf-8"))

    def test_filename_sweep_cannot_take_foreign_matching_files(self):
        matched = self.fixture("qa_foreign.txt")
        other = self.fixture("other.txt")
        before = {path: self.snapshot(path) for path in (matched, other)}
        result = quarantine.sweep_test_files(self.fixtures, "sweep-job")
        self.assertEqual(result["reported"], [str(matched)])
        self.assertEqual(result["moved"], [])
        self.assertEqual(before, {path: self.snapshot(path) for path in (matched, other)})

    def test_missing_path_is_only_a_report_without_source_access(self):
        path = self.fixtures / "does-not-exist.txt"
        result = quarantine.quarantine([path], "job", "snapshot")
        self.assertEqual(result["reported"], [str(path)])
        self.assertFalse(path.exists())

    def test_log_failure_preserves_file_and_is_visible(self):
        path = self.fixture("foreign.txt")
        before = self.snapshot(path)
        with patch.object(quarantine, "_log", return_value=False):
            result = quarantine.quarantine([path], "job", "snapshot")
        self.assertEqual(result["failed"], [str(path)])
        self.assertFalse(result["entries"][0]["log_written"])
        self.assertEqual(self.snapshot(path), before)

    def test_report_log_cannot_escape_workspace(self):
        path = self.fixture("foreign.txt")
        outside = self.root / "outside.log"
        with patch.object(quarantine, "QLOG", outside):
            result = quarantine.quarantine([path], "job", "snapshot")
        self.assertEqual(result["failed"], [str(path)])
        self.assertFalse(outside.exists())
        self.assertTrue(path.exists())

    def test_claimed_verified_flag_is_not_an_authorization_api(self):
        path = self.fixture("foreign.txt")
        with self.assertRaises(TypeError):
            quarantine.quarantine([path], "job", "snapshot", verified=True)
        self.assertTrue(path.exists())

    @unittest.skipUnless(os.name == "nt", "Windows Run-as contract")
    def test_runas_reports_snapshot_difference_without_moving_foreign_file(self):
        path = self.fixture("windows_diagnostic_simulation.dat")
        before = self.snapshot(path)
        cwd = self.root / "workspace/hermes_tasks"
        cwd.mkdir(parents=True)

        class Adv:
            def CreateProcessWithLogonW(self, *args):
                return True

        class Kernel:
            def WaitForSingleObject(self, *args):
                return 0

            def GetExitCodeProcess(self, handle, code):
                ctypes.cast(code, ctypes.POINTER(ctypes.c_ulong)).contents.value = 0

            def CloseHandle(self, *args):
                pass

        with patch.multiple(runas, ROOT=self.root, HERMES_HOME=self.root / "runtime"), \
                patch.object(runas, "_api", return_value=(Adv(), Kernel())), \
                patch.object(runas, "_read_credential", return_value=("fixture-user", "not-a-credential")), \
                patch.object(runas, "_scan_root", side_effect=[set(), {str(path)}]), \
                patch.object(runas, "_scan_profile", return_value=set()), \
                patch.object(runas, "_simple_as", return_value=0), \
                patch("subprocess.run", side_effect=AssertionError("no real subprocess")), \
                patch("shutil.move", side_effect=AssertionError("no quarantine move")):
            result = runas.run(["hermes-fixture.exe", "chat"], cwd=str(cwd), timeout=1,
                               base_env={}, user="fixture-user", credential_target="fixture-only")
        self.assertEqual(result.returncode, 97)
        self.assertIn("HERMES_UNATTRIBUTED_PATHS", result.stderr)
        self.assertNotIn("HERMES_BREAKOUT", result.stderr)
        self.assertEqual(self.snapshot(path), before)
        self.assertFalse(self.qroot.exists())


if __name__ == "__main__":
    unittest.main()



def _skip_without_hermes_runtime(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(self):
        try:
            return fn(self)
        except runas.RunAsUnavailable as exc:
            if "HERMES_INSTALLED_RUNTIME_UNAVAILABLE" in str(exc):
                self.skipTest("Hermes-Laufzeit nicht installiert (HERMES_INSTALLED_RUNTIME_UNAVAILABLE)")
            raise
    return wrapper


QuarantineSafetyTests.test_runas_reports_snapshot_difference_without_moving_foreign_file = _skip_without_hermes_runtime(
    QuarantineSafetyTests.test_runas_reports_snapshot_difference_without_moving_foreign_file)
