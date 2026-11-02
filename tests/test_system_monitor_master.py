#!/usr/bin/env python3
"""System-Monitor Master-Tests — lauffähig mit: python tests/test_system_monitor_master.py"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Projektroot auf sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestSystemMonitor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from nexus.computer.monitor import SystemMonitor, HISTORY_FILE, history_read
        cls.SystemMonitor = SystemMonitor
        cls.HISTORY_FILE = HISTORY_FILE
        cls.history_read = history_read
        cls.mon = SystemMonitor()

    def test_collect_keys(self):
        data = self.mon.collect(write_history=True)
        self.assertTrue(data.get("success"))
        for key in (
            "cpu", "ram", "disk", "disks", "gpu", "network",
            "processes", "ollama", "nexus", "alerts", "platform", "timestamp",
        ):
            self.assertIn(key, data, f"missing key: {key}")

    def test_cpu_ram_numeric_or_null(self):
        data = self.mon.collect(write_history=False)
        cpu = data["cpu"]
        ram = data["ram"]
        # percent: number or None (never invent)
        self.assertTrue(cpu.get("percent") is None or isinstance(cpu.get("percent"), (int, float)))
        self.assertTrue(ram.get("percent") is None or isinstance(ram.get("percent"), (int, float)))
        if cpu.get("percent") is not None:
            self.assertGreaterEqual(cpu["percent"], 0)
            self.assertLessEqual(cpu["percent"], 100.0 + 50)  # allow slight over on loadavg
        if ram.get("percent") is not None:
            self.assertGreaterEqual(ram["percent"], 0)
            self.assertLessEqual(ram["percent"], 100.0)

    def test_disks_list(self):
        data = self.mon.collect(write_history=False)
        self.assertIsInstance(data["disks"], list)
        self.assertGreaterEqual(len(data["disks"]), 1)
        d0 = data["disks"][0]
        self.assertIn("mount", d0)

    def test_processes_list(self):
        data = self.mon.collect(write_history=False)
        procs = data["processes"]
        self.assertIn("top_cpu", procs)
        self.assertIn("top_ram", procs)
        self.assertIsInstance(procs["top_cpu"], list)
        self.assertIsInstance(procs["top_ram"], list)
        # With psutil: expect entries; without: empty + note
        if procs.get("source") == "psutil":
            # May still be empty in restricted envs — just type-check entries
            for p in procs["top_cpu"]:
                self.assertIn("name", p)
                self.assertIn("pid", p)

    def test_ollama_dict(self):
        data = self.mon.collect(write_history=False)
        o = data["ollama"]
        self.assertIsInstance(o, dict)
        self.assertIn("reachable", o)
        self.assertIn("models", o)
        self.assertIsInstance(o["models"], list)

    def test_alerts_list(self):
        data = self.mon.collect(write_history=False)
        self.assertIsInstance(data["alerts"], list)
        for a in data["alerts"]:
            self.assertIn("level", a)
            self.assertIn("code", a)
            self.assertIn("message_de", a)

    def test_history_write(self):
        before = type(self).history_read(5)
        self.mon.collect(write_history=True)
        self.assertTrue(self.HISTORY_FILE.exists())
        after = type(self).history_read(5)
        self.assertGreaterEqual(len(after), 1)
        last = after[-1]
        self.assertIn("timestamp", last)
        self.assertIn("cpu", last)

    def test_summary_de_nonempty(self):
        s = self.mon.summary_de()
        self.assertIsInstance(s, str)
        self.assertTrue(len(s.strip()) > 5)

    def test_snapshot_alias(self):
        s = self.mon.snapshot()
        self.assertIn("cpu", s)

    def test_answer_query(self):
        r = self.mon.answer_query("cpu auslastung")
        self.assertTrue(len(r) > 3)
        r2 = self.mon.answer_query("ist ollama")
        self.assertTrue("Ollama" in r2 or "ollama" in r2.lower())

    def test_gpu_skip_if_na(self):
        """GPU percent may be null — must not invent numbers."""
        data = self.mon.collect(write_history=False)
        gpu = data["gpu"]
        if gpu.get("gpus"):
            for g in gpu["gpus"]:
                pct = g.get("percent")
                self.assertTrue(pct is None or isinstance(pct, (int, float)))
        else:
            self.assertFalse(gpu.get("available") and not gpu.get("note"))

    def test_metrics_compat(self):
        from nexus.computer.metrics import metrics, summary_de, SystemMetrics
        m = metrics()
        self.assertIn("cpu", m)
        self.assertIn("ram", m)
        self.assertTrue(len(summary_de(m)) > 3)
        sm = SystemMetrics()
        self.assertIn("cpu", sm.collect())


def main():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestSystemMonitor)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
