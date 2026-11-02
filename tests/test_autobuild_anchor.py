import unittest
from nexus.autobuild.loop import _anchor_hit


class AnchorTests(unittest.TestCase):
    def test_literal_with_paren(self):
        self.assertTrue(_anchor_hit("def search(", "    def search(self, query: str):"))
        self.assertTrue(_anchor_hit("def _protect(", "def _protect(text: str) -> str:"))
        self.assertTrue(_anchor_hit("def feasibility(", "def feasibility() -> dict:"))

    def test_invalid_regex_no_exception(self):
        self.assertFalse(_anchor_hit("def search(", "def other():"))

    def test_existing_escaped_regex_still_works(self):
        self.assertTrue(_anchor_hit(r"def keep_alive_value\(", "def keep_alive_value(x):"))
        self.assertTrue(_anchor_hit(r"raise\ ValueError\(\"NO_JSON\"\)", 'raise ValueError("NO_JSON")'))
        self.assertTrue(_anchor_hit('links["CORE_CHATGPT"]', 'x = links["CORE_CHATGPT"]'))

    def test_empty(self):
        self.assertFalse(_anchor_hit("", "abc"))
