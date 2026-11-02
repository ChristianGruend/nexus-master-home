import json, os, shutil, time, unittest
from pathlib import Path
from unittest import mock
from nexus.crew import fixer_actions as fx
from nexus.crew import direct

BASE = Path(__file__).resolve().parents[1] / "workspace" / "tmp" / "nexus_fixer_test"

class T(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(BASE, ignore_errors=True); (BASE / "logs").mkdir(parents=True)
    def tearDown(self):
        shutil.rmtree(BASE, ignore_errors=True)

    def test_safe_rejects_c(self):
        with self.assertRaises(PermissionError):
            fx._safe(Path("C:/Windows/x.txt"))

    def test_whitelist_refuses_unknown(self):
        r = fx.run([], BASE, actions=["format_c"])
        self.assertEqual(r[0]["result"], "FAIL"); self.assertIn("Whitelist", r[0]["detail"])

    def test_ack_requires_health_and_start(self):
        (BASE / "logs" / "nexus_control.log").write_text("[2026-10-27T19:02:18] START local verified; x\n")
        e = ["nexus_control.log: [2026-10-27T17:35:13] ERROR NEXUS startup rc=1",
             "nexus_os_status.log: 2026-10-25T13:00:55+0100 SYSTEM_HEALTH DEGRADED -> CRITICAL"]
        with mock.patch.object(fx, "health_ok", return_value=False):
            self.assertFalse(fx.ack_stale(BASE, e)["ok"])
            self.assertEqual(fx.plan(e, BASE)[-1], "restart_nexus")
        with mock.patch.object(fx, "health_ok", return_value=True):
            self.assertIn("ack_stale", fx.plan(e, BASE))
            self.assertTrue(fx.ack_stale(BASE, e)["ok"])
        self.assertTrue(all(fx.is_acked(x, BASE) for x in e))
        self.assertFalse(fx.is_acked("2026-10-27T20:00:00+0100 SYSTEM_HEALTH OK -> CRITICAL", BASE))

    def test_rotate_zips_then_removes_old_only(self):
        old = BASE / "logs" / "ollama-old.log"; old.write_text("x" * 1000)
        new = BASE / "logs" / "ollama-new.log"; new.write_text("y")
        t = time.time() - 5 * 86400; os.utime(old, (t, t))
        r = fx.rotate_logs(BASE)
        self.assertTrue(r["ok"]); self.assertFalse(old.exists()); self.assertTrue(new.exists())
        z = list((BASE / "logs" / "_archive").glob("*.zip")); self.assertEqual(len(z), 1)

    def test_unload_verifies(self):
        calls = iter([{"models": [{"name": "m"}]}, {}, {"models": []}])
        with mock.patch.object(fx, "_ollama", side_effect=lambda *a, **k: next(calls)):
            self.assertTrue(fx.unload_ollama(BASE)["ok"])

    def test_fixer_prepare_reports_pass_fail(self):
        with mock.patch.object(direct, "last_errors", side_effect=[["x: [2026-10-27T17:35:13] ERROR NEXUS startup rc=1"], []]), \
             mock.patch.object(fx, "plan", return_value=["ack_stale"]), \
             mock.patch.object(fx, "run", return_value=[{"action": "ack_stale", "result": "PASS", "detail": "ok"}]):
            r = direct.prepare({"tag": "Fixer"}, "behebe den fehler", None)
        self.assertIn("Gesamt: PASS", r["final"])

if __name__ == "__main__":
    unittest.main()
