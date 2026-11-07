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

from nexus.studio import imgtools


class T(unittest.TestCase):
    def test_each_tool_new_image_with_size(self):
        a = Image.new("RGB", (300, 200), (10, 120, 200))
        exp = {"crop": ({"box": (0, 0, 100, 50)}, (100, 50)), "rotate": ({"deg": 90}, (200, 300)),
               "flip": ({"mode": "h"}, (300, 200)), "resize": ({"w": 150, "h": 100}, (150, 100)),
               "watermark": ({"text": "NEXUS"}, (300, 200)), "border": ({"px": 10}, (320, 220))}
        for n, (p, size) in exp.items():
            b = imgtools.tool(a, n, **p)
            self.assertIsNot(b, a); self.assertEqual(b.size, size, n)
        self.assertEqual(a.getpixel((0, 0)), (10, 120, 200))
