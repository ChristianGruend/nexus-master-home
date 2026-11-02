import unittest
from unittest import mock
from nexus.crew import direct
from nexus.master import evidence

class FakeR:
    def __init__(self): self.calls = []
    def search(self, q, limit=6, news=False):
        self.calls.append((q, news))
        return {"success": True, "source": "Bing News", "results": [
            {"title": "Schwarzer Oktober: PHP", "url": "https://www.welt.de/x", "snippet": "s"}]}

class M: pass

class T(unittest.TestCase):
    def test_scout_today_uses_live_news_search(self):
        m = M(); m.research = FakeR()
        r = direct.prepare({"tag": "Scout"}, "@Scout was ist heute zum schwarzen oktober neues dazu gekommen", m)
        self.assertEqual(m.research.calls, [("schwarzen oktober", True)])
        self.assertIn("https://www.welt.de/x", r["final"]); self.assertIn("Stand", r["final"])
        self.assertEqual(r["tools"][0]["name"], "web_search")

    def test_no_hits_is_honest(self):
        m = M(); m.research = mock.Mock(); m.research.search.return_value = {"results": [], "tried": ["Bing"]}
        r = direct.prepare({"tag": "Coder"}, "@Coder recherchiere quantenfoo", m)
        self.assertIn("keine passenden Treffer", r["final"])

    def test_fake_tool_call_text_blocked(self):
        out = evidence.guard_output({"intent": "crew", "reply": 'Suche gestartet {"name": "duck_duck_go_search", "arguments": {"query": "x"}}', "data": {}})
        self.assertNotIn("duck_duck_go_search", str(out.get("reply")))
        self.assertIn("FAKE_TOOL_CALL", str(out))

    def test_live_tool_result_passes_guard(self):
        out = evidence.guard_output({"intent": "crew", "reply": "Gesamt: PASS", "data": {"tools": [{"name": "x"}], "provenance": "live_tool_result"}})
        self.assertEqual(out["reply"], "Gesamt: PASS")

if __name__ == "__main__":
    unittest.main()
