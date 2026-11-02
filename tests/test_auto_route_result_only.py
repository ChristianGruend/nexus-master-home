import unittest
from nexus.master.decision import decide
from nexus.crew.direct import result_only

class T(unittest.TestCase):
    def test_auto_routes(self):
        self.assertEqual(decide("was ist heute zum schwarzen oktober neues dazu gekommen")["agent_id"], "scout")
        self.assertEqual(decide("schreib mir ein python skript das dateien zählt")["agent_id"], "coder")
        self.assertEqual(decide("behebe den fehler")["agent_id"], "fixer")
        self.assertEqual(decide("öffne notepad")["intent"], "os_launch_app")
        self.assertEqual(decide("Wie geht es dir?")["intent"], "chat")
    def test_mention_has_priority(self):
        self.assertEqual(decide("@Coder was ist heute neu")["agent_id"], "coder")
    def test_result_only(self):
        t = '(1) Gerade erledigt: web_search OK\n(2) Als Nächstes: Antwort\n@Scout übernimmt.\nIch suche nach X\n{"name": "duck_duck_go_search", "arguments": {"q": "x"}}\nErgebnis:\n1. Titel\n   https://a.de'
        self.assertEqual(result_only(t), "1. Titel\n   https://a.de")

if __name__ == "__main__":
    unittest.main()
