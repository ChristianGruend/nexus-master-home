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

import numpy as np
from nexus.studio import imgtools


class T(unittest.TestCase):
    def test_spot_removed(self):
        a = Image.new("RGB", (100, 100), (120, 120, 120)); a.paste((0, 0, 0), (45, 45, 55, 55))
        m = Image.new("L", (100, 100), 0); m.paste(255, (43, 43, 57, 57))
        b = np.asarray(imgtools.retouch(a, m)).astype(int)
        self.assertLess(abs(b[50, 50] - 120).max(), 25)
        self.assertEqual(np.asarray(a)[50, 50].tolist(), [0, 0, 0])
