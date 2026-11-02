"""derive_plan: JSON-Dateiinhalt als Objekt wird zu String serialisiert, Grenzen bleiben."""
import json
import unittest
from types import SimpleNamespace

from nexus.integrations.hermes_brain import HermesBrain


class _M:
    def __init__(self, plan):
        self.plan = plan

    def chat(self, messages, **kw):
        return {"success": True, "model": "offline-test", "reply": json.dumps(self.plan)}


GOAL = "Erstelle einen Arbeitsplan, eine JSON-Statusdatei und einen Bericht."


class PlanJsonContent(unittest.TestCase):
    def test_json_object_content_is_serialized(self):
        plan = {"files": [
            {"path": "workspace/projects/hermes/plan.txt", "content": "Plan"},
            {"path": "workspace/projects/hermes/status.json", "content": {"status": "laufend", "schritte": []}},
            {"path": "workspace/projects/hermes/bericht.md", "content": "# Bericht"}]}
        r = HermesBrain(SimpleNamespace(llm=_M(plan))).derive_plan(GOAL)
        self.assertTrue(r["success"], r)
        js = [x for x in r["requirements"] if x["path"].endswith(".json")][0]
        self.assertIsInstance(js["content"], str)
        self.assertEqual(json.loads(js["content"])["status"], "laufend")

    def test_object_content_for_non_json_file_still_invalid(self):
        plan = {"files": [{"path": "workspace/projects/hermes/plan.md", "content": {"x": 1}}]}
        r = HermesBrain(SimpleNamespace(llm=_M(plan))).derive_plan(GOAL)
        self.assertEqual(r["error"], "PLAN_STRUCTURE_INVALID")

    def test_json_object_outside_area_still_rejected(self):
        plan = {"files": [{"path": "C:/Windows/x.json", "content": {"x": 1}}]}
        r = HermesBrain(SimpleNamespace(llm=_M(plan))).derive_plan(GOAL)
        self.assertFalse(r["success"])
        self.assertEqual(r["error"], "WRITE_PATH_NOT_ALLOWED")


if __name__ == "__main__":
    unittest.main()
