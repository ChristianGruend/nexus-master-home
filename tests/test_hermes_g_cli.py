"""Exercise the G-only shell-argument adapter without starting Hermes."""
import importlib
import os
import sys
import types
import unittest
from unittest.mock import patch


class GPathAdapterTests(unittest.TestCase):
    def test_output_paths_are_relative_and_other_paths_unchanged(self):
        from nexus.integrations import hermes_g_cli

        class Operations:
            env = types.SimpleNamespace(cwd=r'G:\meine-ki\nexus-ai\workspace\hermes_out')
            cwd = env.cwd

            def _escape_shell_arg(self, arg, translate_path=True):
                return arg

        def convert(path):
            return 'G:' + path[2:].replace('/', '\\') if path.startswith('/g/') else path

        results = []

        def cli():
            op = Operations()
            root = op.env.cwd
            results.extend([op._escape_shell_arg(root),
                            op._escape_shell_arg(root + r'\nested\file.txt'),
                            op._escape_shell_arg('/g/meine-ki/nexus-ai/workspace/hermes_out/file.txt'),
                            op._escape_shell_arg(root + r'_other\file.txt'),
                            op._escape_shell_arg(root, translate_path=False)])
            op.env = types.SimpleNamespace(cwd=r'G:\somewhere_else')
            with self.assertRaisesRegex(RuntimeError, 'OUTPUT_CWD_MISMATCH'):
                op._escape_shell_arg(root + r'\file.txt')

        modules = {'hermes_bootstrap': types.ModuleType('hermes_bootstrap'),
                   'tools.file_operations': types.SimpleNamespace(ShellFileOperations=Operations),
                   'tools.environments.local': types.SimpleNamespace(_msys_to_windows_path=convert),
                   'hermes_cli.main': types.SimpleNamespace(main=cli)}
        env = {'TERMINAL_CWD': Operations.cwd,
               'HERMES_HOME': r'G:\meine-ki\nexus-ai\workspace\hermes_arbeitsbereich\nexus-hermes-home',
               'NEXUS_HERMES_INSTALL': r'C:\existing\hermes-agent'}
        with patch.dict(sys.modules, modules), patch.dict(os.environ, env), patch.object(sys, 'path', list(sys.path)), patch.object(hermes_g_cli, '_install_mcp_discovery_guard'):
            hermes_g_cli.main()
        self.assertEqual(results[:3], ['.', 'nested/file.txt', 'file.txt'])
        self.assertEqual(results[3], Operations.cwd + r'_other\file.txt')
        self.assertEqual(results[4], Operations.cwd)


if __name__ == '__main__':
    unittest.main()
