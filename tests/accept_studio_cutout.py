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

from nexus.studio import cutout


class T(unittest.TestCase):
    def test_png_alpha_model_on_g(self):
        self.assertTrue(str(cutout.MODEL).upper().startswith("G:"))
        d = tmpdir(self); src = img(d, "p.jpg")
        r = cutout.remove_bg(src, d / "o.png")
        self.assertTrue(r["success"], r); o = Image.open(d / "o.png")
        self.assertEqual(o.mode, "RGBA"); self.assertEqual(o.size, (320, 240))
