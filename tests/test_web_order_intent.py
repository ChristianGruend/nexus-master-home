"""Web-Artefakte von lokalen Aufträgen und deren Einschränkungen unterscheiden."""
from __future__ import annotations

import unittest

from nexus.master.web_order import is_web_order


class WebOrderIntentTests(unittest.TestCase):
    def test_browser_mentions_do_not_redirect_local_integration(self):
        for goal in (
            "Baue die lokale Qwen3-/ADB-Integration ohne Browserzugriff.",
            "Implementiere die lokale Qwen3-/ADB-Integration. Browser ist nicht verfügbar.",
            "Entwickle die lokale Qwen3-/ADB-Integration. Der Browser bleibt geschlossen.",
            "Implementiere Qwen3 und ADB, keine Browseroberfläche.",
            "Baue die Qwen3-/ADB-Integration ohne HTML.",
            "Implementiere die lokale Qwen3-/ADB-Integration ohne HTML/CSS/JavaScript.",
            "Implementiere Qwen3 und ADB. Eine Webseite wird nicht benötigt.",
        ):
            with self.subTest(goal=goal):
                self.assertFalse(is_web_order(goal))

    def test_actual_web_artifacts_keep_their_route(self):
        for goal in (
            "Baue eine HTML-Seite mit CSS und JavaScript.",
            "Entwickle ein Canvas-Spiel.",
            "Baut mir ein spielbares Weltraumspiel.",
            "Baue ein Browsergame.",
            "Entwickle ein Browser-Projekt.",
            "Implementiere eine Browseroberfläche.",
            "Baue eine Website.",
            "Entwickle eine Web-App.",
            "Baue einen Musikplayer.",
            "Baue einen Player im Browser, ohne Internetzugriff.",
            "Entwickle nicht nur eine Webseite, sondern einen Musikplayer.",
        ):
            with self.subTest(goal=goal):
                self.assertTrue(is_web_order(goal))

    def test_python_questions_and_negated_builds_stay_out(self):
        for goal in (
            "Baue ein Snake-Spiel in Python.",
            "Wie baue ich ein Browsergame?",
            "Baue keine Webseite.",
            "Entwickle kein Browsergame.",
        ):
            with self.subTest(goal=goal):
                self.assertFalse(is_web_order(goal))


if __name__ == "__main__":
    unittest.main()
