#!/usr/bin/env python3
"""Smoke: decide() für Run-Script / Build-Intents + Shell/Config Timeouts."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestDecideBuildScripts(unittest.TestCase):
    def test_capabilities_still_early(self):
        from nexus.master.decision import decide
        d = decide("was kannst du")
        self.assertEqual(d["intent"], "capabilities")
        self.assertTrue(d.get("early"))

    def test_diagnose_not_capabilities(self):
        from nexus.master.decision import decide
        d = decide("die meisten funktionen funktionieren nicht")
        self.assertEqual(d["intent"], "diagnose")
        self.assertTrue(d.get("early"))

    def test_apps_open_intact(self):
        from nexus.master.decision import decide
        d = decide("öffne apps")
        self.assertEqual(d["intent"], "ui")
        self.assertEqual(d.get("action"), "apps_open")

    def test_run_script_phrase(self):
        from nexus.master.decision import decide
        d = decide("führe skript workspace/scripts/New-ShaderProject.ps1 aus")
        self.assertEqual(d["intent"], "run_script")
        self.assertTrue(d.get("early"))
        self.assertIn("New-ShaderProject.ps1", d.get("path", ""))

    def test_run_script_setup_ai(self):
        from nexus.master.decision import decide
        d = decide("run script Setup-AI-Agent.ps1")
        self.assertEqual(d["intent"], "run_script")
        self.assertTrue(d.get("early"))
        self.assertIn("Setup-AI-Agent.ps1", d.get("path", ""))

    def test_baue_shader_force_tools(self):
        from nexus.master.decision import decide
        d = decide("baue shader projekt")
        self.assertEqual(d["intent"], "build")
        self.assertTrue(d.get("force_tools"))
        self.assertEqual(d.get("timeout_hint"), 600)
        self.assertIn("New-ShaderProject", d.get("script_hint") or "")

    def test_mein_agent_force_tools(self):
        from nexus.master.decision import decide
        d = decide("lege mein-agent an")
        # may hit build via "lege" early or ai-agent path
        self.assertIn(d["intent"], ("build", "run_script"))
        if d["intent"] == "build":
            self.assertTrue(d.get("force_tools"))
            self.assertEqual(d.get("timeout_hint"), 600)

    def test_npm_create_force_tools(self):
        from nexus.master.decision import decide
        d = decide("npm create vite projekt")
        self.assertEqual(d["intent"], "build")
        self.assertTrue(d.get("force_tools"))

    def test_ai_agent_script_hint(self):
        from nexus.master.decision import decide
        d = decide("setup ai-agent bitte")
        self.assertEqual(d["intent"], "build")
        self.assertIn("Setup-AI-Agent", d.get("script_hint") or "")


class TestConfigShellTimeouts(unittest.TestCase):
    def test_defaults(self):
        from nexus.common.config import Config
        self.assertGreaterEqual(Config.SHELL_TIMEOUT, 300)
        self.assertGreaterEqual(Config.SHELL_TIMEOUT_BUILD, 600)
        self.assertGreaterEqual(Config.SHELL_TIMEOUT_MAX, 900)
        self.assertGreaterEqual(Config.SHELL_STDOUT_MAX, 12000)

    def test_clamp(self):
        from nexus.computer.shell import _clamp_timeout
        self.assertEqual(_clamp_timeout(9999), 900)
        self.assertEqual(_clamp_timeout(None, default=600), 600)
        self.assertEqual(_clamp_timeout(120), 120)


class TestScriptsExist(unittest.TestCase):
    def test_templates(self):
        scripts = ROOT / "workspace" / "scripts"
        self.assertTrue((scripts / "New-ShaderProject.ps1").is_file())
        self.assertTrue((scripts / "Setup-AI-Agent.ps1").is_file())
        shader = (scripts / "New-ShaderProject.ps1").read_text(encoding="utf-8")
        self.assertIn("mainImage", shader)
        self.assertIn("react-shaders", shader)
        self.assertIn("my-vfx-app", shader)
        agent = (scripts / "Setup-AI-Agent.ps1").read_text(encoding="utf-8")
        self.assertIn("ai-agent-app", agent)
        self.assertIn("readFile", agent)
        self.assertIn("writeFile", agent)
        self.assertIn("listFiles", agent)
        self.assertIn("calculate", agent)
        self.assertIn("systemInfo", agent)
        self.assertIn("inputSchema", agent)
        self.assertIn("ollama", agent.lower())
        self.assertIn("npx tsx server/server.ts", agent)


class TestShellRunScriptResolve(unittest.TestCase):
    def test_resolve_by_name(self):
        from nexus.computer.shell import ShellExecutor
        sh = ShellExecutor()
        p = sh._resolve_script("New-ShaderProject.ps1")
        self.assertIsNotNone(p)
        self.assertTrue(p.name == "New-ShaderProject.ps1")

    def test_run_script_ps1_on_linux_notes(self):
        """Auf Box ohne pwsh: klarer Fehler, Skript-Pfad gemeldet."""
        from nexus.computer.shell import ShellExecutor
        import platform
        if platform.system() == "Windows":
            self.skipTest("Windows — würde echt laufen")
        sh = ShellExecutor()
        r = sh.run_script("New-ShaderProject.ps1", timeout=5)
        # either pwsh ran (unlikely) or ps1_needs_windows
        self.assertIn(r.get("error"), ("ps1_needs_windows", "timeout", "script_not_found", None) )
        if r.get("error") == "ps1_needs_windows":
            self.assertIn("New-ShaderProject", r.get("script") or r.get("stderr") or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
