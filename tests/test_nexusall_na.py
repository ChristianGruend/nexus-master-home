"""NA01-NA06 NEXUS All Ausbau (20261028)."""
from pathlib import Path
import pytest
from nexus.nexusall import core

STATIC = Path(__file__).resolve().parents[1] / "nexus" / "chat" / "static"
HTML = (STATIC / "nexusall.html").read_text(encoding="utf-8-sig")


@pytest.fixture()
def media(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    monkeypatch.setattr(core, "WORKSPACE", ws)
    monkeypatch.setattr(core, "LIB", ws / "media" / "nexusall")
    monkeypatch.setattr(core, "DIRS", {k: ws / "media" / "nexusall" / v.name for k, v in core.DIRS.items()})
    monkeypatch.setattr(core, "STATE", ws / "nexusall")
    monkeypatch.setattr(core, "DB", ws / "nexusall" / "t.db")
    monkeypatch.setattr(core, "TMP", ws / "tmp")
    monkeypatch.setattr(core, "TRASH", ws / "media" / ".papierkorb")
    monkeypatch.setattr(core, "ffprobe_path", lambda: None)
    core.ensure_dirs()
    (core.DIRS["video"] / "film.mp4").write_bytes(b"v" * 10)
    (core.DIRS["audio"] / "lied.mp3").write_bytes(b"a" * 10)
    return ws


def test_na01_trash_restore_purge(media):
    lib = core.library()
    assert {i["tab"] for i in lib["items"]} == {"filme", "musik"}
    r = core.trash_item("nexusall/Videos/film.mp4")
    assert r["success"] and not (core.DIRS["video"] / "film.mp4").exists()
    assert all(i["name"] != "film" for i in core.library()["items"])  # Papierkorb nicht in Bibliothek
    t = core.trash_list()
    assert t["count"] == 1 and t["items"][0]["origin"] == "nexusall/Videos/film.mp4"
    assert core.trash_restore(t["items"][0]["trash_id"])["restored"] == "nexusall/Videos/film.mp4"
    assert (core.DIRS["video"] / "film.mp4").exists()
    tid = core.trash_item("nexusall/Musik/lied.mp3")["trash_id"]
    assert core.trash_purge("../x")["success"] is False
    assert core.trash_purge(tid)["success"] and core.trash_list()["count"] == 0


def test_na01_trash_blocks_traversal(media):
    assert core.trash_item("../../etc")["success"] is False
    assert core.trash_restore("..\\x")["success"] is False


def test_na02_progress_parse():
    p = core.parse_progress("NXP2|downloading|5000000|20000000|NA|1250000.5|12")
    assert p == {"speed": 1250000.5, "eta": 12.0, "downloaded": 5000000.0, "total": 20000000.0, "progress": 25.0}
    p = core.parse_progress("NXP2|downloading|100|NA|400|NA|NA")
    assert p["progress"] == 25.0 and p["speed"] is None
    assert core.parse_progress("NXP2|finished|1|1|NA|NA|NA")["progress"] == 100.0
    assert core.parse_progress("[download] irgendwas") is None


def test_na02_cmd_template_and_loudnorm(monkeypatch):
    monkeypatch.setattr(core, "ffmpeg_path", lambda: None)
    c = core._build_cmd({"quality": "audio_m4a", "url": "https://x.org/a", "subs": "", "loudnorm": 1})
    s = " ".join(c)
    assert "NXP2|%(progress.status)s" in s and "--audio-format m4a" in s
    assert "ExtractAudio:-af " + core.LOUDNORM in c[c.index("--postprocessor-args") + 1]
    assert str(core.DIRS["audio"]) in c
    v = core._build_cmd({"quality": "best", "url": "https://x.org/a", "subs": "", "loudnorm": 1})
    assert "--postprocessor-args" not in v


def test_na04_no_white_tile():
    assert "#frames{flex:1;position:relative;background:#fff" not in HTML
    assert 'id="start"' in HTML and "startVis()" in HTML


def test_na05_glass_design():
    for tok in ("backdrop-filter:blur", "--ac:#89cff0", "--line:#c3cad4", "--tx:#14213d"):
        assert tok in HTML


def test_na03_na06_ui_present():
    for tok in ('data-tab="filme"', 'data-tab="musik"', 'data-tab="papierkorb"', 'value="audio_m4a"', "/api/nexusall/delete",
                "createDynamicsCompressor", "lowshelf", "peaking", "FX.target=-14" if False else "target:-14",
                'data-pre="film"', 'data-pre="musik"', 'data-pre="sprache"', 'id="fxAuto"', "role=\"progressbar\"", "fmtEta"):
        assert tok in HTML, tok
from pathlib import Path
from nexus.nexusall import core
def test_ogg_in_videos_is_film():
    assert core._kind(Path("media/nexusall/Videos/a.ogg")) == "video"
    assert core._kind(Path("media/nexusall/Musik/a.ogg")) == "audio"
def test_kill_tree_exists():
    assert callable(core._kill_tree)
def test_build_cmd_forces_progress():
    cmd = core._build_cmd({"id": "x", "url": "https://e.x/a.mp4", "quality": "best", "subs": "", "loudnorm": 0})
    assert "--progress" in cmd and "--newline" in cmd
def test_library_stats_tabs():
    s = core.library_stats()
    assert s["success"] and set(("filme", "musik")) <= set(s) and "count" in s["filme"]
