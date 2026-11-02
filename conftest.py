"""C: tabu (Eddie, 20261028): pytest-Temp, tempfile und Kindprozesse auf G: umleiten.

Laedt vor jeder Testsammlung (rootdir-conftest). Kein fester --basetemp, damit parallele Laeufe
(Autobuild-Engine + manueller Start) sich nicht gegenseitig loeschen; pytest nummeriert
G:\\meine-ki\\nexus-ai\\workspace\\pytest_tmp\\pytest-of-<user>\\pytest-N selbst.
"""
import os
import tempfile
from pathlib import Path

_TMP = Path(__file__).resolve().parent / "workspace" / "pytest_tmp"
_TMP.mkdir(parents=True, exist_ok=True)
for _k in ("TEMP", "TMP", "TMPDIR"):
    os.environ[_k] = str(_TMP)
tempfile.tempdir = str(_TMP)


def pytest_configure(config):
    if str(Path(tempfile.gettempdir()).resolve()).lower().startswith("c:"):
        raise RuntimeError("C_DRIVE_BLOCKED: pytest-Temp liegt auf C:")
