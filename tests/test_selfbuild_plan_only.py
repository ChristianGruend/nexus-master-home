"""t329u Regression: Plan-/Pruef-/Statusauftraege ueber NEXUS sind kein Selbstbau."""
import unittest
from nexus.master.nexus_order import is_nexus_change


class PlanOnlyNotSelfBuild(unittest.TestCase):
    def test_plan_task_is_not_change(self):
        for t in ("Testauftrag: Erstelle einen kurzen Aufgabenplan (max 5 Schritte) fuer: NEXUS Master Home nach Neustart verifizieren.",
                  "Pruefe den Status der NEXUS UI.",
                  "Schreib einen Bericht ueber das NEXUS Dashboard."):
            self.assertFalse(is_nexus_change(t), t)

    def test_real_change_still_detected(self):
        for t in ("Bau in NEXUS Master Home einen Musik-Button.",
                  "Behebe den Fehler in der NEXUS Oberflaeche.",
                  "Erstelle einen Plan und integriere ihn in die NEXUS UI."):
            self.assertTrue(is_nexus_change(t), t)


class StatusIntentLong(unittest.TestCase):
    def test_long_check_is_status(self):
        from nexus.master.decision import decide
        self.assertEqual(decide("Pruefe Core und Status und nenne den Health-Score.")["intent"], "status")
        self.assertNotEqual(decide("Bau eine Statusanzeige in die NEXUS UI und pruefe sie danach bitte gruendlich.")["intent"], "status")


class GptLocalRoute(unittest.TestCase):
    def test_local_agent_config(self):
        from nexus.integrations import gpt_local_agent as g
        self.assertEqual((g.PARTNER, g.PROVIDER, g.MODEL), ("gpt", "ollama", "qwen3.5:9b"))


if __name__ == "__main__":
    unittest.main()