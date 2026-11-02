from nexus.integrations import hermes_agent as h
from pathlib import Path

def test_exe_resolves_installed_runtime_not_ps_shim():
    exe = Path(h._exe())
    assert "hermes-ps" not in str(exe).lower()
    base = exe.parent.parent
    if exe.is_file():
        assert (base / "hermes-agent" / "hermes_bootstrap.py").is_file()
        assert list((base / "tools").glob("python-*/python.exe"))

def test_hermes_timeout_300_and_wait_label():
    assert h.TIMEOUT >= 300
    js = (Path(h.__file__).resolve().parents[1] / "chat" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "Hermes lädt Modell…" in js
