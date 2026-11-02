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

from nexus.studio import batch


class T(unittest.TestCase):
    def test_n_in_n_out(self):
        d = tmpdir(self); ims = [img(d, f"{i}.png") for i in range(3)]; before = [p.read_bytes() for p in ims]
        r = batch.batch(ims, d / "out", op="bw", size=100)
        self.assertEqual(len(r["outputs"]), 3)
        for o in r["outputs"]:
            self.assertLessEqual(max(Image.open(o).size), 100)
        self.assertEqual([p.read_bytes() for p in ims], before)
