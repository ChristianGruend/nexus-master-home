"""Hermes readiness checks: no processes, credentials, jobs or live network.

Run: python -B -m unittest discover -s tests -p test_hermes_health.py -v
"""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from nexus.integrations import hermes_agent as agent
from nexus.integrations import hermes_runas as runas


class HermesHealthTests(unittest.TestCase):
    def setUp(self):
        settings = {"enabled": False, "run_as": "gustl", "switch": "default_off",
                    "credential_target": "nexus-hermes"}
        self.settings = self.enterContext(patch.object(runas, "settings", return_value=settings))
        self.credential = self.enterContext(patch.object(runas, "credential_present", return_value=True))
        self.exe = self.enterContext(patch.object(agent, "_exe", return_value="hermes.exe"))
        self.is_file = self.enterContext(patch.object(agent.Path, "is_file", return_value=True))
        self.models = self.enterContext(patch.object(agent, "_ollama_models", return_value=[agent.MODEL]))
        self.run = self.enterContext(patch.object(agent.subprocess, "run"))
        self.popen = self.enterContext(patch.object(agent.subprocess, "Popen"))
        self.run_as = self.enterContext(patch.object(runas, "run"))

    def tearDown(self):
        self.run.assert_not_called()
        self.popen.assert_not_called()
        self.run_as.assert_not_called()

    def enable_run_as(self):
        self.settings.return_value = {"enabled": True, "run_as": "nexus-hermes", "switch": "file",
                                      "credential_target": "nexus-hermes"}

    def test_ready_is_explicitly_unverified_without_starting_cli(self):
        result = agent.health()
        self.assertTrue(result["reachable"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["cli"], "hermes.exe")
        self.assertIsNone(result["version"])
        self.assertEqual(result["version_source"], "not_probed")
        self.assertEqual(result["cli_probe"], "path_exists")
        self.assertFalse(result["execution_verified"])
        self.credential.assert_not_called()

    def test_missing_cli_does_not_query_ollama(self):
        self.is_file.return_value = False
        result = agent.health()
        self.assertFalse(result["reachable"])
        self.assertEqual(result["error"], "HERMES_UNAVAILABLE")
        self.assertIsNone(result["cli"])
        self.models.assert_not_called()

    def test_cli_access_error_is_unavailable(self):
        self.is_file.side_effect = PermissionError("private local detail")
        result = agent.health()
        self.assertFalse(result["reachable"])
        self.assertEqual(result["detail"], "PermissionError")
        self.models.assert_not_called()

    def test_missing_run_as_credential_does_not_fall_back(self):
        self.enable_run_as()
        self.credential.return_value = False
        result = agent.health()
        self.assertFalse(result["reachable"])
        self.assertEqual(result["error"], "HERMES_UNAVAILABLE")
        self.assertEqual(result["run_as"], "nexus-hermes")
        self.exe.assert_not_called()
        self.models.assert_not_called()

    def test_run_as_credential_api_failure_is_unavailable(self):
        self.enable_run_as()
        self.credential.side_effect = runas.RunAsUnavailable("RUN_AS_REQUIRES_WINDOWS")
        result = agent.health()
        self.assertFalse(result["reachable"])
        self.exe.assert_not_called()
        self.models.assert_not_called()

    def test_run_as_readiness_does_not_launch_a_probe(self):
        self.enable_run_as()
        result = agent.health()
        self.assertTrue(result["reachable"])
        self.assertFalse(result["execution_verified"])
        self.assertEqual(result["run_as"], "nexus-hermes")
        self.credential.assert_called_once_with("nexus-hermes")

    def test_model_must_be_present(self):
        self.models.return_value = ["different-model:latest"]
        result = agent.health()
        self.assertFalse(result["reachable"])
        self.assertEqual(result["detail"], f"Modell {agent.MODEL} fehlt")

    def test_latest_model_alias_is_supported(self):
        self.models.return_value = [agent.MODEL + ":latest"]
        self.assertTrue(agent.health()["reachable"])

    def test_ollama_timeout_is_reported_without_claiming_readiness(self):
        self.models.side_effect = TimeoutError()
        result = agent.health()
        self.assertFalse(result["reachable"])
        self.assertEqual(result["detail"], "Ollama: TimeoutError")
        self.assertFalse(result["execution_verified"])


class OllamaHealthTimeoutTests(unittest.TestCase):
    def test_model_lookup_has_short_timeout_and_only_reads_tags(self):
        payload = json.dumps({"models": [{"name": agent.MODEL}]}).encode("utf-8")
        with patch.object(agent.urllib.request, "urlopen", return_value=io.BytesIO(payload)) as request:
            self.assertEqual(agent._ollama_models(), [agent.MODEL])
        request.assert_called_once_with(agent.OLLAMA + "/api/tags", timeout=2)


if __name__ == "__main__":
    unittest.main()
