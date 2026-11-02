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
    def test_slideshow_and_still(self):
        d = tmpdir(self); ims = [img(d, f"{i}.png", color=(40 * i, 90, 150)) for i in range(3)]
        video.slideshow(ims, d / "s.mp4", per_image=2.0)
        self.assertAlmostEqual(video.probe(d / "s.mp4")["duration"], 6.0, delta=0.6)
        video.still(d / "s.mp4", d / "f.png", at=1.0)
        self.assertEqual(Image.open(d / "f.png").format, "PNG")
