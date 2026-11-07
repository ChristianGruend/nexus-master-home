"""Container-Tauglichkeit: Laufzeitpfade liegen unter workspace/ (Code-Verzeichnis ist im Image read-only)."""
from pathlib import Path

from nexus.common import paths
from nexus.phone_actions import LOG_PATH as PHONE_LOG
from nexus.voice.wakeword_service import LOG_PATH as WAKE_LOG

CODE_PATHS = {"ROOT", "STATIC_DIR"}


def test_runtime_paths_under_workspace():
    ws = paths.WORKSPACE.resolve()
    runtime = {k: v for k, v in vars(paths).items()
               if k.isupper() and isinstance(v, Path) and k not in CODE_PATHS}
    outside = {k: str(v) for k, v in runtime.items() if not v.resolve().is_relative_to(ws)}
    assert outside == {}


def test_module_logs_under_workspace():
    ws = paths.WORKSPACE.resolve()
    assert PHONE_LOG.resolve().is_relative_to(ws)
    assert WAKE_LOG.resolve().is_relative_to(ws)
