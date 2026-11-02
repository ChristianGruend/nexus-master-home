import unittest
from unittest import mock
from nexus.knowledge.research import Research

WALL = "<html>anomaly-modal bots use DuckDuckGo too</html>"
NEWS = ('<a class="title" target="_blank" href="https://www.welt.de/php-sicherheitsluecke-der-schwarze-oktober">'
        'PHP-Sicherheitsluecke: der schwarze Oktober</a>'
        '<a class="title" href="https://example.com/fussball">Fussball heute</a>')

class T(unittest.TestCase):
    def test_terms(self):
        self.assertEqual(Research()._terms("schwarzen oktober php"), ["schwa", "oktob", "php"])

    def test_ddg_blocked_falls_back_to_relevant_news(self):
        R = Research()
        with mock.patch.object(R, "_fetch_ddg", return_value=(WALL, None)), \
             mock.patch.object(R, "_is_bot_wall", return_value=True), \
             mock.patch.object(R, "_fetch_ddg_lite", return_value=(WALL, None)), \
             mock.patch.object(R, "_fetch_bing", return_value=("<html></html>", None)), \
             mock.patch.object(R, "_fetch_bing_news", return_value=(NEWS, None)), \
             mock.patch.object(R, "_wikipedia", return_value=[]), \
             mock.patch("nexus.knowledge.research.time.sleep"):
            r = R.search("schwarzen oktober php")
        self.assertTrue(r["success"]); self.assertEqual(r["source"], "Bing News")
        self.assertEqual(len(r["results"]), 1); self.assertIn("welt.de", r["results"][0]["url"])

    def test_irrelevant_results_are_not_returned(self):
        R = Research()
        with mock.patch.object(R, "_fetch_ddg", return_value=(None, "x")), \
             mock.patch.object(R, "_fetch_ddg_lite", return_value=(None, "x")), \
             mock.patch.object(R, "_fetch_bing", return_value=(None, "x")), \
             mock.patch.object(R, "_fetch_bing_news", return_value=('<a class="title" href="https://ex.com/a">Gemeinde Schwarzen</a>', None)), \
             mock.patch.object(R, "_wikipedia", return_value=[{"title": "E-Mail-Spoofing", "url": "https://de.wikipedia.org/wiki/E", "snippet": "x"}]), \
             mock.patch("nexus.knowledge.research.time.sleep"):
            r = R.search("schwarzen oktober php")
        self.assertFalse(r["success"]); self.assertIn("SEARCH_FAIL", r["reply"]); self.assertEqual(r["results"], [])

    def test_ddg_lite_parser(self):
        h = "<a rel=\"nofollow\" href=\"//duckduckgo.com/l/?uddg=https%3A%2F%2Fphp.net%2F\" class='result-link'>PHP</a>"
        self.assertEqual(Research()._parse_ddg_lite(h, 5)[0]["url"], "https://php.net/")

if __name__ == "__main__":
    unittest.main()
