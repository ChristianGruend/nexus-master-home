"""Offline startup failure injection; no service, token, task store or files changed."""
from __future__ import annotations

import builtins
import contextlib
import io
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class ServerBridgeStartupTest(unittest.TestCase):
    def load_server(self, bridge, *, import_error=None):
        """Execute the actual server module with inert application dependencies."""
        dependencies = {
            "nexus.chat.engine": SimpleNamespace(
                IDENTITY_FIELDS=(), NexusEngine=Mock(return_value=object()),
                response_identity=Mock()),
            "nexus.chat.shared_room": SimpleNamespace(SharedRoom=Mock(return_value=object())),
            "nexus.master.activity": SimpleNamespace(get_activity_log=Mock()),
            "nexus.common.config": SimpleNamespace(Config=SimpleNamespace(HOST="127.0.0.1", PORT=8299)),
            "nexus.common.paths": SimpleNamespace(
                STATIC_DIR=ROOT, MEDIA_DIR=ROOT, DOWNLOADS_DIR=ROOT,
                SCREENS_DIR=ROOT, ensure_dirs=Mock()),
            "nexus.bridge": SimpleNamespace(api=bridge),
            "nexus.automation": SimpleNamespace(api=object()),
            "nexus.multimodal": SimpleNamespace(api=object()),
        }
        original_import = builtins.__import__

        def import_dependency(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "nexus.bridge" and import_error is not None:
                raise import_error
            if name in dependencies:
                return dependencies[name]
            return original_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=import_dependency), \
                patch.dict(os.environ, {"NEXUS_ASSISTANT_TOKEN": "synthetic-test-token"}), \
                patch.object(sys, "path", sys.path.copy()), \
                contextlib.redirect_stdout(io.StringIO()):
            return runpy.run_path(str(ROOT / "nexus/chat/server.py"), run_name="startup_contract")

    def test_bridge_import_failure_aborts_server_module(self):
        bridge = SimpleNamespace(boot=Mock())
        with self.assertRaisesRegex(ImportError, "synthetic missing Bridge dependency"):
            self.load_server(bridge, import_error=ImportError("synthetic missing Bridge dependency"))
        bridge.boot.assert_not_called()

    def test_bridge_boot_failure_aborts_server_module(self):
        bridge = SimpleNamespace(boot=Mock(side_effect=RuntimeError("synthetic executor bind failure")))
        with self.assertRaisesRegex(RuntimeError, "synthetic executor bind failure"):
            self.load_server(bridge)
        bridge.boot.assert_called_once_with()

    def test_healthy_server_retains_booted_bridge(self):
        bridge = SimpleNamespace(boot=Mock(return_value={"token_present": True}))
        loaded = self.load_server(bridge)
        self.assertIs(loaded["bridge_api"], bridge)
        bridge.boot.assert_called_once_with()
        self.assertTrue(callable(loaded["main"]))


class BridgeManagerPublicationTest(unittest.TestCase):
    def test_failed_executor_binding_is_unpublished_and_retryable(self):
        from nexus.bridge import api

        failed_candidate = object()
        healthy_candidate = object()
        calls = []

        def bind(candidate):
            # Neither requests nor a retry may observe an incompletely bound manager.
            self.assertIsNone(api._MANAGER)
            calls.append(candidate)
            if candidate is failed_candidate:
                raise RuntimeError("synthetic incomplete registry")

        with patch.object(api, "_MANAGER", None), patch.object(api, "_TOKEN_BOOT", {}), \
                patch.object(api, "ensure_bridge_token", return_value=("synthetic-test-token", None, False)), \
                patch.object(api, "TaskManager", side_effect=[failed_candidate, healthy_candidate]) as factory, \
                patch.object(api, "bind_default_executors", side_effect=bind) as binder, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "synthetic incomplete registry"):
                api.boot()
            self.assertIsNone(api._MANAGER)
            self.assertEqual(api._TOKEN_BOOT, {})

            info = api.boot()
            self.assertIs(api._MANAGER, healthy_candidate)
            self.assertTrue(info["token_present"])
            self.assertIs(api.manager(), healthy_candidate)
            api.boot()
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(binder.call_count, 2)
            self.assertEqual(calls, [failed_candidate, healthy_candidate])

    def test_token_failure_never_constructs_a_manager(self):
        from nexus.bridge import api

        with patch.object(api, "_MANAGER", None), patch.object(api, "_TOKEN_BOOT", {}), \
                patch.object(api, "ensure_bridge_token", side_effect=OSError("synthetic unreadable token")), \
                patch.object(api, "TaskManager") as factory:
            with self.assertRaisesRegex(OSError, "synthetic unreadable token"):
                api.boot()
            factory.assert_not_called()
            self.assertIsNone(api._MANAGER)
            self.assertEqual(api._TOKEN_BOOT, {})


if __name__ == "__main__":
    unittest.main()
