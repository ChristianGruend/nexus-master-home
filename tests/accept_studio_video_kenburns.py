import shutil, tempfile, unittest
from pathlib import Path
from PIL import Image

TMPB = Path(r"G:\meine-ki\nexus-ai\workspace\tmp")


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


class T(unittest.TestCase):
    def test_ken_burns_mp4(self):
        d = tmpdir(self); src = img(d); before = src.read_bytes()
        r = video.ken_burns(src, d / "kb.mp4", seconds=3)
        info = video.probe(d / "kb.mp4")
        self.assertTrue(r["success"]); self.assertAlmostEqual(info["duration"], 3, delta=0.5)
        self.assertIn("keine KI", info["comment"]); self.assertEqual(src.read_bytes(), before)
