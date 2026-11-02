"""NA08: DOM-Pruefung NEXUS All per Headless-Edge (laeuft nur, wenn Server + Edge vorhanden)."""
import os, sys, urllib.request
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workspace" / "tmp"))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

def _up():
    try:
        return urllib.request.urlopen("http://127.0.0.1:8299/api/nexusall/health", timeout=5).status == 200
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not (os.path.exists(EDGE) and _up()), reason="Edge/Server nicht verfuegbar")

@pytest.mark.parametrize("w,h", [(1600, 900), (1920, 1080), (900, 1200)])
def test_nexusall_layout(w, h):
    import na08_dom
    r = na08_dom.run(w, h)
    assert "error" not in r, r
    assert r["overlap"] == [] and r["offscreen"] == [] and r["clipped"] == [], r
    assert r["low_contrast"] == [] and r["contrast_min"] >= 4.5, r
    assert r["empty_blocks"] == [] and r["fallback_missing"] == 0, r
    if r["cards"]["n"]:
        assert len(r["cards"]["w"]) == 1 and len(r["cards"]["h"]) == 1, r["cards"]
        assert r["cards"]["h"][0] >= 0.6 * r["cards"]["w"][0], r["cards"]
        assert r["lib_h"] >= r["cards"]["h"][0], r
