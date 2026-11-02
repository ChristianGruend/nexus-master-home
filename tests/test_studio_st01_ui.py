"""ST01: Studio-Layout (links Menue, Vorher/Nachher, Schnellaktionen, Kategorien, Fotos, Varianten, FX)."""
from pathlib import Path

R = Path(__file__).resolve().parents[1] / "nexus" / "chat" / "static"


def test_st01_layout_parts_present():
    h = (R / "studio.html").read_text(encoding="utf-8", errors="replace")
    for part in ('id="nav"', 'id="ba"', 'class="pan qa"', 'id="cats"', 'id="lib"', 'id="vars"', "studio_fx.js"):
        assert part in h, part
    js = (R / "js" / "studio_fx.js").read_text(encoding="utf-8")
    for part in ("/api/studio/filter", "/api/studio/adjust", "/api/studio/social", "fxHist", "preview:true"):
        assert part in js, part
