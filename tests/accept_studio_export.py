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

from nexus.studio import export


class T(unittest.TestCase):
    def test_export_copies_original_untouched(self):
        d = tmpdir(self); src = img(d); before = src.read_bytes(); dst = d / "lib"
        r1 = export.export_version(src, dst); r2 = export.export_version(src, dst)
        self.assertTrue(r1["success"]); self.assertNotEqual(r1["out"], r2["out"])
        self.assertEqual(Path(r1["out"]).read_bytes(), before); self.assertEqual(src.read_bytes(), before)

    def test_refuses_c_drive(self):
        d = tmpdir(self)
        self.assertFalse(export.export_version(img(d), r"C:\\nexus_export_test")["success"])
