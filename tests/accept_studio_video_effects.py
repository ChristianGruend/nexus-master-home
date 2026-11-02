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
    def test_overlay_and_gif(self):
        d = tmpdir(self); src = d / "src.mp4"
        video._run(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2", "-pix_fmt", "yuv420p", str(src)])
        self.assertTrue(video.effects(src, d / "o.mp4", text="Hallo")["success"])
        self.assertGreater((d / "o.mp4").stat().st_size, 0)
        self.assertTrue(video.effects(src, d / "o.gif", gif=True)["success"])
        self.assertEqual((d / "o.gif").read_bytes()[:3], b"GIF")
