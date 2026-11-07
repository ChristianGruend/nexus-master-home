import shutil, tempfile, unittest
from pathlib import Path
from PIL import Image

TMPB = Path(__file__).resolve().parent.parent / "workspace" / "tmp"


def tmpdir(tc):
    TMPB.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="st_acc_", dir=str(TMPB)))
    tc.addCleanup(shutil.rmtree, d, True)
    return d


def img(d, name="a.png", size=(320, 240), color=(200, 60, 40)):
    p = d / name
    Image.new("RGB", size, color).save(p)
    return p

from nexus.studio import video


def clip(d, sec=4):
    out = d / "src.mp4"
    video._run(["-f", "lavfi", "-i", f"testsrc=size=640x360:rate=25:duration={sec}", "-pix_fmt", "yuv420p", str(out)])
    return out


class T(unittest.TestCase):
    def test_trim_and_vertical(self):
        d = tmpdir(self); src = clip(d)
        video.trim(src, d / "t.mp4", 1.0, 3.0)
        self.assertAlmostEqual(video.probe(d / "t.mp4")["duration"], 2.0, delta=0.5)
        video.vertical(src, d / "v.mp4")
        i = video.probe(d / "v.mp4"); self.assertEqual((i["width"], i["height"]), (1080, 1920))
