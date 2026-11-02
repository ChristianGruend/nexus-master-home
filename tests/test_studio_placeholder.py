"""SB01-Abnahmetest: llava-Platzhalter duerfen nicht als subject gespeichert werden."""
import io
import json

import pytest
from PIL import Image

from nexus.studio import core
from nexus.studio.core import Studio


@pytest.mark.parametrize("ph", ["one or two English words, e.g. dog", "5 to 8 short German keywords"])
def test_placeholder_subject_dropped(monkeypatch, ph):
    base = core.ROOT / "workspace" / "autobuild" / "_t_sb01"
    import shutil
    shutil.rmtree(base, ignore_errors=True)
    st = Studio(base)
    (base / "thumbs").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), (90, 90, 90)).save(base / "thumbs" / "x1.jpg")
    st.items["x1"] = {"id": "x1", "name": "x.jpg"}

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    payload = {"response": json.dumps({"main": "animal", "subject": ph, "keywords": ["Hund"], "description": "Ein Hund."})}
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *a, **k: R(json.dumps(payload).encode()))
    monkeypatch.setattr(core, "_live", lambda *a, **k: None)
    try:
        it = st.categorize("x1")
        assert "english" not in it["subject"] and "keywords" not in it["subject"] and "one or two" not in it["subject"]
    finally:
        shutil.rmtree(base, ignore_errors=True)
