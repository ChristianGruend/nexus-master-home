"""Run the installed Hermes CLI with G-output-local shell paths.

Deployed by hermes_runas into its existing read-only query directory. No C:
installation is patched. File-tool guards and Windows permissions stay active.
"""
from pathlib import Path
import ntpath
import os
import sys


def _install_mcp_discovery_guard():
    from hermes_cli import mcp_startup

    original = mcp_startup.start_background_mcp_discovery

    def configured_discovery(*args, **kwargs):
        # Hermes marks an empty profile as started before checking its config.
        # A later call then reports a failed discovery that never ran. Keep
        # the existing config probe (including plugin MCP and its conservative
        # error handling) ahead of that state change on every call.
        if not mcp_startup._has_configured_mcp_servers():
            return None
        return original(*args, **kwargs)

    mcp_startup.start_background_mcp_discovery = configured_discovery


def main():
    output = Path(os.environ['TERMINAL_CWD'])
    home = Path(os.environ['HERMES_HOME'])
    if output.drive.upper() != 'G:' or output.name != 'hermes_out' or home.drive.upper() != 'G:':
        raise RuntimeError('G_OUTPUT_AND_HOME_REQUIRED')
    install = Path(os.environ['NEXUS_HERMES_INSTALL'])
    sys.path.insert(0, str(install))
    # Keep the existing CLI dependency activation before importing file tools.
    import hermes_bootstrap  # noqa: F401
    from tools.file_operations import ShellFileOperations
    from tools.environments.local import _msys_to_windows_path

    original = ShellFileOperations._escape_shell_arg
    output_key = ntpath.normcase(ntpath.normpath(str(output)))

    def output_local_arg(self, arg, translate_path=True):
        if translate_path and arg:
            native = ntpath.normpath(_msys_to_windows_path(str(arg)))
            key = ntpath.normcase(native)
            if key == output_key or key.startswith(output_key + '\\'):
                effective = getattr(self.env, 'cwd', None) or self.cwd
                if ntpath.normcase(ntpath.normpath(str(effective))) != output_key:
                    raise RuntimeError('OUTPUT_CWD_MISMATCH')
                arg = ntpath.relpath(native, str(output)).replace('\\', '/')
        return original(self, arg, translate_path=translate_path)

    ShellFileOperations._escape_shell_arg = output_local_arg
    _install_mcp_discovery_guard()
    from hermes_cli.main import main as cli_main
    return cli_main()


if __name__ == '__main__':
    sys.exit(main())
