"""Crew liefert bei klaren Auftraegen direkt (offline, ohne Netz)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nexus.crew import direct


class FakeResearch:
    def __init__(self, results):
        self.results, self.queries = results, []

    def search(self, q, limit=6):
        self.queries.append(q)
        return {"success": True, "source": "Test", "results": self.results}


HITS = [{"title": "Python Kurs - YouTube", "url": "https://www.youtube.com/watch?v=abc", "snippet": "Kurs"},
        {"title": "Blog", "url": "https://example.org/x", "snippet": "kein YT"}]


class CrewDirectTests(unittest.TestCase):
    def test_coder_youtube_returns_only_real_youtube_links(self):
        r = FakeResearch(HITS)
        out = direct.prepare({"tag": "Coder"}, "@Coder zeig mir ein link für youtube mit dessen thema", SimpleNamespace(research=r))
        self.assertIn("https://www.youtube.com/watch?v=abc", out["final"])
        self.assertNotIn("example.org", out["final"])
        self.assertEqual(r.queries, ["youtube Programmieren lernen Tutorial deutsch"])

    def test_scout_named_topic_is_used(self):
        r = FakeResearch(HITS)
        direct.prepare({"tag": "Scout"}, "@Scout youtube video über Raketenbau", SimpleNamespace(research=r))
        self.assertEqual(r.queries, ["youtube raketenbau"])

    def test_no_youtube_hit_falls_back(self):
        r = FakeResearch([HITS[1]])
        direct.youtube_page_search = lambda topic, limit=3: []
        self.assertIsNone(direct.prepare({"tag": "Coder"}, "youtube link", SimpleNamespace(research=r)))

    def test_other_agent_or_message_untouched(self):
        self.assertIsNone(direct.prepare({"tag": "Media"}, "youtube link", SimpleNamespace(research=FakeResearch(HITS))))
        self.assertIsNone(direct.prepare({"tag": "Fixer"}, "wie geht es dir", SimpleNamespace()))

    def test_fixer_reads_real_log_errors(self):
        with tempfile.TemporaryDirectory() as d:
            logs = Path(d) / "logs"; logs.mkdir()
            (logs / "nexus_server.err.log").write_text(
                'ok line\n127.0.0.1 - "GET /api/bridge/health HTTP/1.1" 401 -\nTraceback: ValueError boom\n', encoding="utf-8")
            errs = direct.last_errors(Path(d))
            self.assertEqual(len(errs), 1)
            self.assertIn("ValueError boom", errs[0])

    def test_fixer_trigger(self):
        self.assertTrue(direct.FIX_RE.search("@fixer behebe den fehler"))


if __name__ == "__main__":
    unittest.main()


class YoutubeParseTests(unittest.TestCase):
    def test_parse_results_page(self):
        html = 'x"videoRenderer":{"videoId":"abcdefghijk","thumbnail":{},"title":{"runs":[{"text":"Kurs \\u0026 Mehr"}]}'
        out = direct.parse_youtube_results(html)
        self.assertEqual(out[0]["url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(out[0]["title"], "Kurs & Mehr")
