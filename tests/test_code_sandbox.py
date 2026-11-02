"""Offline contracts for the bounded code executor and the static checker.

Only a tiny, self-authored probe written into a TemporaryDirectory inside the
released code area is ever executed; it is removed afterwards. The network probe
is expected to be blocked by the in-process guard.
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from nexus.bridge import code_sandbox as cs
from nexus.common.paths import ROOT

MAIN = "def add(a, b):\n    return a + b\n"
TEST_OK = "from rechner import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
TEST_FAIL = "assert 1 == 2, 'boom'\n"
TEST_NET = "import socket\nsocket.socket()\n"
TEST_LOOP = "while True:\n    pass\n"
CODE_BASE = ROOT / "workspace/projects/hermes/code"


class CodePathTests(unittest.TestCase):
    def test_accepts_code_area(self):
        self.assertEqual(cs.code_path("workspace/projects/hermes/code/p/x.py"),
                         "workspace/projects/hermes/code/p/x.py")
        self.assertIsNotNone(cs.code_path("workspace/projects/hermes/code/p/sub/x.md"))

    def test_rejects_escape_and_shape(self):
        for value in ("workspace/projects/hermes/plan_hermes.txt",
                      "workspace/projects/hermes/code/a/../b.py",
                      "workspace/projects/hermes/code/x.txt",
                      "workspace/projects/hermes/code/con.py",
                      "workspace/projects/hermes/code/a/b/c/d/e.py",
                      "../code/x.py",
                      None, 5):
            with self.subTest(value=value):
                self.assertIsNone(cs.code_path(value))


class _Probe(unittest.TestCase):
    prefix = "p_"

    def setUp(self):
        CODE_BASE.mkdir(parents=True, exist_ok=True)
        self.proj = Path(tempfile.mkdtemp(prefix=self.prefix, dir=str(CODE_BASE)))

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)

    def _rel(self, name):
        return "workspace/projects/hermes/code/" + self.proj.name + "/" + name


class StaticCheckTests(_Probe):
    prefix = "chk_"

    def setUp(self):
        super().setUp()
        (self.proj / "good.py").write_text(MAIN, encoding="utf-8")
        (self.proj / "evil.py").write_text("import socket\nimport os\nos.system('x')\n", encoding="utf-8")
        (self.proj / "broken.py").write_text("def (:\n", encoding="utf-8")
        (self.proj / "uses_local.py").write_text("from good import add\nx = add(1, 2)\n", encoding="utf-8")
        (self.proj / "thirdparty.py").write_text("import pytest\n", encoding="utf-8")
        (self.proj / "missing_symbol.py").write_text("from good import nope\n", encoding="utf-8")

    def test_ok(self):
        r = cs.check_sources(ROOT, self._rel("good.py"))
        self.assertEqual(r["syntax"], "ok")
        self.assertEqual(r["forbidden"], [])
        self.assertEqual(r["external"], [])
        self.assertEqual(r["missing"], [])
        self.assertIn("add", r["defs"])

    def test_forbidden(self):
        r = cs.check_sources(ROOT, self._rel("evil.py"))
        self.assertEqual(r["syntax"], "ok")
        self.assertIn("import socket", r["forbidden"])
        self.assertIn("os.system", r["forbidden"])

    def test_local_dependency_resolves(self):
        r = cs.check_sources(ROOT, self._rel("uses_local.py"))
        self.assertEqual(r["local"], ["good"])
        self.assertEqual(r["external"], [])
        self.assertEqual(r["missing"], [])

    def test_third_party_is_external(self):
        r = cs.check_sources(ROOT, self._rel("thirdparty.py"))
        self.assertEqual(r["external"], ["pytest"])
        self.assertNotIn("pytest", r["stdlib"])

    def test_missing_symbol_reported(self):
        r = cs.check_sources(ROOT, self._rel("missing_symbol.py"))
        self.assertEqual(r["missing"], ["good.nope"])

    def test_syntax_error(self):
        r = cs.check_sources(ROOT, self._rel("broken.py"))
        self.assertEqual(r["syntax"], "error")
        self.assertEqual(r["forbidden"], [])
        self.assertEqual(r["external"], [])
        self.assertEqual(r["missing"], [])

    def test_rejects_non_py(self):
        with self.assertRaises(cs.SandboxError):
            cs.check_sources(ROOT, "workspace/projects/hermes/code/x/y.md")


class ExecutorTests(_Probe):
    prefix = "run_"

    def setUp(self):
        super().setUp()
        (self.proj / "rechner.py").write_text(MAIN, encoding="utf-8")
        (self.proj / "test_rechner.py").write_text(TEST_OK, encoding="utf-8")
        (self.proj / "test_fail.py").write_text(TEST_FAIL, encoding="utf-8")
        (self.proj / "test_net.py").write_text(TEST_NET, encoding="utf-8")
        (self.proj / "test_loop.py").write_text(TEST_LOOP, encoding="utf-8")

    def test_disabled_by_default(self):
        # Ohne belegte AppContainer-Isolation bleibt der Job-Object-Altpfad gesperrt.
        self.assertFalse(cs.execution_enabled())
        with mock.patch.object(cs, "_appcontainer_ready", return_value=False),              self.assertRaises(cs.SandboxError) as ctx:
            cs.run_code_tests(ROOT, self._rel("test_rechner.py"))
        self.assertEqual(str(ctx.exception), "CODE_EXECUTION_DISABLED")

    def test_verified_appcontainer_is_used_without_global_flag(self):
        with mock.patch.object(cs, "_appcontainer_ready", return_value=True),              mock.patch.object(cs, "_run_isolated", return_value={"containment": "windows_appcontainer"}) as iso:
            out = cs.run_code_tests(ROOT, self._rel("test_rechner.py"))
        self.assertEqual(out["containment"], "windows_appcontainer")
        iso.assert_called_once()

    def test_rejects_non_py_and_outside(self):
        for bad in ("workspace/projects/hermes/code/x/y.md",
                    "workspace/projects/hermes/plan_hermes.txt",
                    "workspace/projects/hermes/code/../x.py"):
            with self.subTest(bad=bad), self.assertRaises(cs.SandboxError):
                cs.run_code_tests(ROOT, bad, allow_exec=True)

    def test_missing_file(self):
        with self.assertRaises(cs.SandboxError) as ctx:
            cs.run_code_tests(ROOT, self._rel("nope.py"), allow_exec=True)
        self.assertEqual(str(ctx.exception), "TEST_FILE_MISSING")

    def test_passing_run_under_containment(self):
        r = cs.run_code_tests(ROOT, self._rel("test_rechner.py"), allow_exec=True)
        self.assertEqual(r["returncode"], 0)
        self.assertFalse(r["timed_out"])
        self.assertTrue(r["sandbox"])
        self.assertTrue(r["env_sanitized"])
        self.assertIn(r["containment"], ("windows_job_object", "posix_rlimit"))
        self.assertEqual(r["network_guard"], "best_effort_inprocess")
        self.assertEqual(r["filesystem_isolation"], "none")
        if os.name == "nt":
            self.assertTrue(r["job_object"])
            self.assertEqual(r["containment"], "windows_job_object")

    def test_failing_run(self):
        r = cs.run_code_tests(ROOT, self._rel("test_fail.py"), allow_exec=True)
        self.assertNotEqual(r["returncode"], 0)

    def test_network_guard_blocks(self):
        r = cs.run_code_tests(ROOT, self._rel("test_net.py"), allow_exec=True)
        self.assertNotEqual(r["returncode"], 0)

    def test_timeout_is_flagged_and_nonzero(self):
        r = cs.run_code_tests(ROOT, self._rel("test_loop.py"), allow_exec=True, timeout=3)
        self.assertTrue(r["timed_out"])
        self.assertNotEqual(r["returncode"], 0)


if __name__ == "__main__":
    unittest.main()
