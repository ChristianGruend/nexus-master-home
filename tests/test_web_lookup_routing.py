import unittest
from nexus.master.decision import decide, web_lookup_query


class WebLookupRouting(unittest.TestCase):
    def test_free_phrases_route_to_research(self):
        for text, q in [("Schau im Netz nach dem Wetter in München", "Wetter in München"),
                        ("NEXUS sollte im Netz was schauen: Bundesliga Tabelle", "Bundesliga Tabelle"),
                        ("Such mal im Internet nach Ollama Neuigkeiten", "Ollama Neuigkeiten")]:
            d = decide(text)
            self.assertEqual(d.get("intent"), "research", text)
            self.assertEqual(d.get("query"), q)

    def test_no_false_positives(self):
        self.assertIsNone(web_lookup_query("Erstelle eine Datei über das Internet"))
        self.assertIsNone(web_lookup_query("Wie geht es dir?"))
        self.assertNotEqual(decide("Wie geht es dir?").get("intent"), "research")

    def test_classic_suche_unchanged(self):
        self.assertEqual(decide("suche Wetter München").get("intent"), "research")


if __name__ == "__main__":
    unittest.main()