import shutil, sqlite3, tempfile, unittest
from pathlib import Path

from nexus.common.paths import WORKSPACE
from nexus.mail import store as S

ROOT = Path(__file__).resolve().parents[1]


class MailStoreTests(unittest.TestCase):
    def setUp(self):
        base = WORKSPACE / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix="mail_ut_", dir=str(base)))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.st = S.MailStore(self.d / "m.db")

    def test_roundtrip_and_persistence(self):
        mid = self.st.add("inbox", "a@x.de", "eddie@x.de", "Hallo NEXUS", "Text 123")
        again = S.MailStore(self.d / "m.db")
        m = again.get(mid)
        self.assertEqual((m["subject"], m["body"], m["folder"]), ("Hallo NEXUS", "Text 123", "inbox"))

    def test_encrypted_at_rest(self):
        self.st.add("inbox", "a@x.de", "b@x.de", "GeheimBetreff", "GeheimText")
        raw = sqlite3.connect(str(self.d / "m.db")).execute("SELECT subject, body FROM messages").fetchone()
        self.assertTrue(raw[0].startswith("dp1:"))
        self.assertNotIn("Geheim", raw[0] + raw[1])

    def test_move_search_folders(self):
        mid = self.st.add("inbox", "a@x.de", "b@x.de", "Rechnung", "bitte zahlen")
        self.assertTrue(self.st.move(mid, "archive"))
        self.assertEqual(self.st.list("inbox"), [])
        self.assertEqual([m["id"] for m in self.st.search("rechnung")], [mid])
        with self.assertRaises(ValueError):
            self.st.move(mid, "spam")

    def test_mail_page_and_route(self):
        html = (ROOT / "nexus" / "chat" / "static" / "mail.html").read_text(encoding="utf-8")
        for word in ("Posteingang", "Gesendet", "Entwürfe", "Archiv", "Papierkorb", "Neue Nachricht", "Einstellungen"):
            self.assertIn(word, html)
        src = (ROOT / "nexus" / "chat" / "server.py").read_text(encoding="utf-8")
        self.assertIn('if path in ("/mail", "/mail.html"):', src)

    def test_no_credentials_in_mail_code(self):
        src = (ROOT / "nexus" / "mail" / "store.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("password =", src)
        self.assertNotIn("app_password", src)
