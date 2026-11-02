import json, tempfile, unittest
from pathlib import Path
from unittest import mock

from nexus.studio import t2i
import shutil


def _tmp(tc):
    base = t2i.WORKSPACE / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="t2i_ut_", dir=str(base)))
    tc.addCleanup(shutil.rmtree, d, True)
    return d


class StudioT2I(unittest.TestCase):
    def test_missing_env_is_honest(self):
        with mock.patch.object(t2i, "T2I_PY", Path(r"G:\nicht\da\python.exe")):
            r = t2i.generate("ein Hund")
        self.assertFalse(r["success"])
        self.assertEqual(r["error"], "T2I_ENV_MISSING")

    def test_empty_prompt(self):
        self.assertEqual(t2i.generate("  ")["error"], "EMPTY_PROMPT")

    def test_generate_with_mocked_subprocess(self):
        tmp = _tmp(self)

        def fake_run(cmd, **kw):
            job = json.loads(Path(cmd[2]).read_text(encoding="utf-8"))[0]
            self.assertEqual((job["w"], job["h"], job["steps"]), (512, 512, 4))
            Path(job["out"]).write_bytes(b"\x89PNG fake")
            self.assertEqual(kw["env"]["HF_HUB_OFFLINE"], "1")
            return mock.Mock(returncode=0, stdout=json.dumps({"ok": True, "out": job["out"], "s": 1.0}) + "\n", stderr="")

        with mock.patch.object(t2i, "feasibility", return_value={"ok": True, "missing": []}), \
                mock.patch.object(t2i.subprocess, "run", side_effect=fake_run):
            r = t2i.generate("Ein Hund am Strand", seed=7, out_dir=tmp)
        self.assertTrue(r["success"], r)
        self.assertTrue(Path(r["path"]).is_file())
        self.assertEqual(list(tmp.glob("job_*.json")), [])

    def test_failure_reported(self):
        tmp = _tmp(self)
        with mock.patch.object(t2i, "feasibility", return_value={"ok": True, "missing": []}), \
                mock.patch.object(t2i.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="", stderr="boom")):
            r = t2i.generate("x", out_dir=tmp)
        self.assertEqual(r["error"], "T2I_FAILED")
        self.assertIn("boom", r["stderr"])

    def test_timeout(self):
        tmp = _tmp(self)
        with mock.patch.object(t2i, "feasibility", return_value={"ok": True, "missing": []}), \
                mock.patch.object(t2i.subprocess, "run", side_effect=t2i.subprocess.TimeoutExpired("x", 1)):
            self.assertEqual(t2i.generate("x", out_dir=tmp, timeout=1)["error"], "T2I_TIMEOUT")
