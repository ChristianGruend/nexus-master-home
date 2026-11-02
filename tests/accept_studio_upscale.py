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

from nexus.studio import upscale


class T(unittest.TestCase):
    def test_x2_x4(self):
        d = tmpdir(self); src = img(d, size=(64, 48))
        for s in (2, 4):
            r = upscale.upscale(src, d / f"u{s}.png", scale=s)
            self.assertTrue(r["success"], r); self.assertIn("sec", r)
            self.assertEqual(Image.open(d / f"u{s}.png").size, (64 * s, 48 * s))
