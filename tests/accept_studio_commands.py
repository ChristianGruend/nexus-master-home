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

from unittest import mock
from nexus.studio import commands


def boom(*a, **k):
    raise RuntimeError("offline")


class T(unittest.TestCase):
    def test_five_sentences(self):
        cases = {"mach es waermer": "warm", "bitte schaerfer machen": "sharpen", "etwas heller bitte": "brighten",
                 "Hintergrund unscharf": "bg_blur", "Insta-Zitat mit Text Hallo": "social_square"}
        with mock.patch("nexus.integrations.gpt_local_agent.ask", side_effect=boom):
            for s, op in cases.items():
                self.assertIn(op, commands.plan_ops(s)["ops"], s)
