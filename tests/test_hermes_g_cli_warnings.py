"""Check discovery routing without importing or starting Hermes."""
import sys
import types
import unittest
from unittest.mock import Mock, patch

from nexus.integrations import hermes_g_cli


class MCPDiscoveryGuardTests(unittest.TestCase):
    def install_guard(self, configured):
        startup = types.SimpleNamespace(
            _has_configured_mcp_servers=Mock(return_value=configured),
            start_background_mcp_discovery=Mock(return_value='discovery-started'),
        )
        original = startup.start_background_mcp_discovery
        package = types.ModuleType('hermes_cli')
        package.mcp_startup = startup
        with patch.dict(sys.modules, {'hermes_cli': package}):
            hermes_g_cli._install_mcp_discovery_guard()
        return startup, original

    def test_empty_profile_never_enters_discovery_state(self):
        startup, original = self.install_guard(False)
        for _ in range(2):
            self.assertIsNone(startup.start_background_mcp_discovery(
                logger=Mock(), thread_name='empty-profile'))
        original.assert_not_called()
        self.assertEqual(startup._has_configured_mcp_servers.call_count, 2)

    def test_configured_or_plugin_servers_keep_original_discovery(self):
        startup, original = self.install_guard(True)
        logger = Mock()
        self.assertEqual(startup.start_background_mcp_discovery(
            logger=logger, thread_name='configured-profile'), 'discovery-started')
        original.assert_called_once_with(logger=logger, thread_name='configured-profile')

    def test_new_server_config_is_not_hidden_by_an_empty_first_call(self):
        startup, original = self.install_guard(False)
        startup.start_background_mcp_discovery(logger=Mock(), thread_name='profile')
        startup._has_configured_mcp_servers.return_value = True
        logger = Mock()
        startup.start_background_mcp_discovery(logger=logger, thread_name='profile')
        original.assert_called_once_with(logger=logger, thread_name='profile')


if __name__ == '__main__':
    unittest.main()
