"""Offline contract tests: project orders are planned, checked and executed through the existing Bridge."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nexus.master import evidence as ev
from nexus.master import project_order as po
from nexus.integrations.hermes_brain import HermesBrain

GOAL = ("Erstelle im freigegebenen Hermes-Arbeitsbereich ein kleines Projekt mit Arbeitsplan, "
        "JSON-Statusdatei und Abschlussbericht. Plane die einzelnen Schritte selbst und verifiziere alle Ergebnisse.")

PLAN3 = {"files": [
    {"path": "workspace/projects/hermes/demo_arbeitsplan.md", "content": "# Arbeitsplan\n1. Schritt\n"},
    {"path": "workspace/projects/hermes/demo_status.json", "content": '{"status": "laufend"}\n'},
    {"path": "workspace/projects/hermes/demo_bericht.md", "content": "# Abschlussbericht\nGeprueft.\n"},
]}

STUFE3 = (
    "# AUTONOMER PRAXISTEST STUFE 3\n"
    "Entwickle eigenständig einen kleinen funktionsfähigen Python-Rechner. "
    "Erstelle das Projekt ausschließlich innerhalb von workspace/projects/hermes/rechner_test/. "
    "Erwartete Ergebnisse: rechner.py mit dem Python-Programm, README.md, testbericht.json. "
    "Falls Python-Ausführung nicht erlaubt ist, melde die Tests als NICHT AUSGEFÜHRT."
)


def answer(name=None, args=None):
    calls = [] if name is None else [{"function": {"name": name, "arguments": args}}]
    return {"success": True, "model": "offline-test", "reply": "done" if not calls else "",
            "message": {"role": "assistant", "content": "done" if not calls else "", "tool_calls": calls},
            "tool_calls": calls}


class Model:
    """Planning calls return JSON, tool calls come from the prepared sequence."""

    def __init__(self, plan, responses=None):
        self.plan = plan
        self.responses = list(responses or [])
        self.plans = []
        self.requests = []

    def chat(self, messages, **kwargs):
        self.requests.append(copy.deepcopy({"messages": messages, **kwargs}))
        if kwargs.get("tools"):
            if not self.responses:
                return answer()
            return self.responses.pop(0)
        self.plans.append(copy.deepcopy(messages))
        reply = self.plan if isinstance(self.plan, str) else json.dumps(self.plan)
        return {"success": True, "model": "offline-test", "reply": reply}


class Bridge:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.records = {}

    def __call__(self, args):
        self.calls.append(copy.deepcopy(args))
        if args["operation"] == "status":
            return copy.deepcopy(self.records[args["task_id"]])
        task_id = "NX-20261009-" + str(len(self.records) + 1).zfill(4)
        if args["task"] == "create_file" and args["params"]["path"] in getattr(self, "fail_paths", ()):
            return {"success": False, "status": "verification_unknown", "error_code": "BRIDGE_UNAVAILABLE",
                    "task_id": None, "result": None}
        if args["task"] == "create_file":
            target = self.root / args["params"]["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(args["params"]["content"].encode("utf-8"))
            payload = {"created": True}
        elif args["task"] == "read_file":
            target = self.root / args["params"]["path"]
            if not target.is_file():  # wie die echte Bridge: fehlende Datei ist ein Fehlerbeleg
                return {"success": False, "status": "failed", "backend_status": "failed",
                        "error_code": "FILE_NOT_FOUND", "task_id": task_id, "result": None}
            payload = {"content": target.read_text(encoding="utf-8"), "truncated": False}
        else:
            payload = {}
        record = {"success": True, "status": "succeeded", "task_id": task_id,
                  "correlation_id": args.get("correlation_id"), "task_readback_verified": True,
                  "execution_instance_verified": True, "result": payload}
        self.records[task_id] = record
        self._persist({"id": task_id, "task": args["task"], "status": "succeeded",
                       "params": copy.deepcopy(args["params"]), "result": payload})
        return copy.deepcopy(record)

    def _persist(self, record):
        """Persisted Bridge-Beleg im Format des echten Task-Speichers."""
        directory = self.root / "workspace" / "bridge" / "tasks"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (record["id"] + ".json")).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def submits(self, task):
        return [c for c in self.calls if c.get("operation") == "submit" and c.get("task") == task]


def responses_for(requirements):
    """One tool batch per four files: all creates, then all reads."""
    out = []
    for index in range(0, len(requirements), 4):
        chunk = requirements[index:index + 4]
        calls = [answer("create_file", r)["tool_calls"][0] for r in chunk]
        calls += [answer("read_file", {"path": r["path"]})["tool_calls"][0] for r in chunk]
        response = answer()
        response["tool_calls"] = calls
        response["message"] = {"role": "assistant", "content": "", "tool_calls": calls}
        out.append(response)
    return out


class ProjectOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bridge = Bridge(self.root)
        self.patcher = patch("nexus.integrations.hermes_client.invoke", self.bridge)
        self.patcher.start()
        # Belege werden wie im Betrieb gelesen, nur aus dem Test-Task-Speicher.
        self.persist = patch.object(ev, "persisted_task", side_effect=self._persisted_task)
        self.persist.start()
        self.master = SimpleNamespace(project_fs=SimpleNamespace(root=self.temp.name), llm=None)
        self.session = {}

    def _persisted_task(self, task_id, root=None):
        path = self.root / "workspace" / "bridge" / "tasks" / (str(task_id) + ".json")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) and value.get("id") == task_id else {}

    def tearDown(self):
        self.persist.stop()
        self.patcher.stop()
        self.temp.cleanup()

    def run_order(self, goal=GOAL, plan=PLAN3, responses=None):
        requirements = plan["files"]
        self.master.llm = Model(plan, responses if responses is not None else responses_for(requirements))
        return po.run(self.master, goal, self.session)

    def state_files(self):
        return list((self.root / "workspace" / "state").glob("hermes-project-order-*.json"))

    # ------------------------------------------------------------------ classification
    def test_explicit_file_operations_are_not_project_orders(self):
        self.assertFalse(po.is_project_order('Erstelle die Datei a.md mit Inhalt: "x"'))
        self.assertFalse(po.is_project_order("Erkläre wie ich eine Datei test.txt schreibe."))
        self.assertFalse(po.is_project_order("Erstelle die neue Markdown-Datei bericht.md mit einem sachlichen Entwicklungsbericht."))
        self.assertTrue(po.is_project_order(GOAL))
        self.assertTrue(po.is_project_order("Erstelle plan.md und status.json.", ["plan.md", "status.json"]))
        self.assertFalse(po.is_project_order("Erstelle plan.md und status.json mit Inhalt: \"x\"",
                                             ["plan.md", "status.json"]))

    def test_named_paths_and_explicit_content_are_detected(self):
        self.assertTrue(po.has_explicit_content('mit Inhalt: "x"'))
        self.assertTrue(po.has_explicit_content('mit Inhalt:\n```text\nzeile\n```'))
        self.assertFalse(po.has_explicit_content(GOAL))

    # ------------------------------------------------------------------ plan derivation
    def test_plan_derivation_rejects_path_outside_released_area(self):
        brain = HermesBrain(SimpleNamespace(llm=Model({"files": [
            {"path": "C:/Windows/secret.txt", "content": "x"}]})))
        result = brain.derive_plan(GOAL)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "WRITE_PATH_NOT_ALLOWED")
        self.assertFalse(self.bridge.calls)

    def test_plan_derivation_requires_json_and_covers_named_files(self):
        brain = HermesBrain(SimpleNamespace(llm=Model("hier steht kein JSON")))
        self.assertEqual(brain.derive_plan(GOAL)["error"], "PLAN_NOT_JSON")
        brain = HermesBrain(SimpleNamespace(llm=Model({"files": [
            {"path": "workspace/projects/hermes/other.md", "content": "x"}]})))
        self.assertEqual(brain.derive_plan(GOAL, required_paths=["workspace/projects/hermes/plan.md"])["error"],
                         "PLAN_REQUIRED_PATH_MISSING")
        brain = HermesBrain(SimpleNamespace(llm=Model({"files": []})))
        self.assertEqual(brain.derive_plan(GOAL)["error"], "PLAN_STRUCTURE_INVALID")

    def test_plan_derivation_never_returns_unchecked_paths(self):
        brain = HermesBrain(SimpleNamespace(llm=Model({"files": [
            {"path": "workspace/projects/hermes/ok.md", "content": "x"},
            {"path": "../../escape.txt", "content": "y"}]})))
        self.assertEqual(brain.derive_plan(GOAL)["error"], "WRITE_PATH_NOT_ALLOWED")

    def test_unsupported_action_goal_is_classified_before_planning(self):
        model = Model({"files": []})
        brain = HermesBrain(SimpleNamespace(llm=model))
        result = brain.derive_plan(STUFE3)
        self.assertEqual(result["error"], "UNSUPPORTED_ACTIONS")
        reasons = " ".join(result["details"])
        self.assertIn("rechner.py", reasons)
        self.assertIn("rechner_test/", reasons)
        self.assertIn("ausführung", reasons)
        self.assertFalse(model.requests)  # keine Modellplanung für nicht unterstützte Aktionen

    def test_truncated_plan_is_classified(self):
        brain = HermesBrain(SimpleNamespace(llm=Model(
            '{"files":[{"path":"workspace/projects/hermes/a.md","content":"x')))
        self.assertEqual(brain.derive_plan(GOAL)["error"], "PLAN_TRUNCATED")

    def test_bare_required_paths_are_normalized_to_the_released_area(self):
        brain = HermesBrain(SimpleNamespace(llm=Model({"files": [
            {"path": "workspace/projects/hermes/plan.md", "content": "x"}]})))
        result = brain.derive_plan(GOAL, required_paths=["plan.md"])
        self.assertTrue(result["success"])
        self.assertEqual(result["requirements"][0]["path"], "workspace/projects/hermes/plan.md")

    def test_unsupported_action_project_order_blocks_with_concrete_reason(self):
        self.master.llm = Model({"files": []})
        out = po.run(self.master, STUFE3, self.session)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["error"], "UNSUPPORTED_PROJECT_ACTION")
        self.assertIn("nicht unterstützte Aktionen", out["reply"])
        self.assertIn("rechner.py", out["reply"])
        self.assertFalse(self.bridge.calls)
        self.assertEqual(self.state_files(), [])

    # ------------------------------------------------------------------ follow-up from existing plans
    def test_followup_reads_existing_plans_and_executes_result(self):
        for name in ("nexus_bestandsaufnahme.md", "nexus_arbeitsplan.md"):
            target = self.root / "workspace" / "projects" / "hermes" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# " + name + "\nAufgabe 1: Test\n", encoding="utf-8")
        goal = ("Lies die vorhandenen Dateien "
                "`workspace/projects/hermes/nexus_bestandsaufnahme.md` und "
                "`workspace/projects/hermes/nexus_arbeitsplan.md`. "
                "Werte die Aufgaben aus und speichere das Ergebnis im freigegebenen Projektbereich.")
        plan = {"files": [{"path": "workspace/projects/hermes/nexus_folge_status.md",
                           "content": "# Folgeauftrag\nErgebnis gespeichert.\n"}]}
        self.master.llm = Model(plan, responses_for(plan["files"]))
        out = po.run(self.master, goal, self.session)
        self.assertEqual(out["status"], "PASS")
        self.assertTrue(out["success"])
        reads = [c["params"]["path"] for c in self.bridge.submits("read_file")]
        self.assertIn("workspace/projects/hermes/nexus_bestandsaufnahme.md", reads)
        self.assertIn("workspace/projects/hermes/nexus_arbeitsplan.md", reads)
        self.assertEqual(len(out["data"]["inputs"]), 2)
        self.assertTrue(all(item["task_id"] for item in out["data"]["inputs"]))
        self.assertTrue((self.root / "workspace" / "projects" / "hermes"
                         / "nexus_folge_status.md").is_file())
        planner = json.dumps(self.master.llm.plans, ensure_ascii=False)
        self.assertIn("nexus_bestandsaufnahme.md", planner)
        self.assertIn("READ ONLY", planner)

    def test_plan_only_goal_reads_no_inputs(self):
        out = self.run_order()
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(self.bridge.submits("read_file") and
                         [c["params"]["path"] for c in self.bridge.submits("read_file")]
                         , [r["path"] for r in PLAN3["files"] for _ in (1, 2)])  # Vorab- + Rücklesen
        self.assertEqual(out["data"]["inputs"], [])

    # ------------------------------------------------------------------ execution
    def test_project_order_executes_all_files_with_plan_and_receipts(self):
        out = self.run_order()
        self.assertEqual(out["status"], "PASS")
        self.assertTrue(out["success"])
        self.assertEqual(out["data"]["order_type"], "project_order")
        self.assertEqual(len(out["plan"]), 3)
        self.assertTrue(all(step["status"] == "PASS" for step in out["plan"]))
        self.assertEqual(len(out["data"]["task_ids"]), 6)
        self.assertIn("AUSFÜHRUNGSPLAN", out["reply"])
        for requirement in PLAN3["files"]:
            target = self.root / requirement["path"]
            self.assertEqual(target.read_text(encoding="utf-8"), requirement["content"])
            self.assertIn(requirement["path"], out["reply"])
        self.assertEqual(len(self.state_files()), 1)
        stored = json.loads(self.state_files()[0].read_text(encoding="utf-8"))
        self.assertEqual(stored["status"], "PASS")
        self.assertEqual(stored["goal"], GOAL)
        self.assertEqual(self.session["hermes_order"]["project"], True)

    def test_completed_project_order_is_not_written_twice(self):
        first = self.run_order()
        self.assertEqual(first["status"], "PASS")
        creates = len(self.bridge.submits("create_file"))
        self.master.llm = Model(PLAN3, [])
        second = po.run(self.master, GOAL, self.session)
        self.assertEqual(second["status"], "PASS")
        self.assertEqual(len(self.bridge.submits("create_file")), creates)
        self.assertEqual(second["data"]["task_ids"], first["data"]["task_ids"])
        self.assertFalse(self.master.llm.requests)

    def test_failed_batch_marks_partial_and_leaves_remaining_steps_planned(self):
        plan = {"files": PLAN3["files"] + [
            {"path": "workspace/projects/hermes/demo_vier.txt", "content": "vier\n"},
            {"path": "workspace/projects/hermes/demo_fuenf.txt", "content": "fuenf\n"}]}
        self.master.llm = Model(plan, [])
        self.bridge.fail_paths = {plan["files"][0]["path"]}  # erste Datei: Bridge-Beleg fehlt
        out = po.run(self.master, GOAL, self.session)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertFalse(out["success"])
        self.assertEqual([s["status"] for s in out["plan"]], ["BLOCKED"] * 4 + ["PLANNED"])
        self.assertEqual(len(self.bridge.submits("create_file")), 1)  # kein Weiterschreiben nach Fehler

    def test_partial_progress_is_reported_after_completed_batch(self):
        plan = {"files": PLAN3["files"] + [
            {"path": "workspace/projects/hermes/demo_vier.txt", "content": "vier\n"},
            {"path": "workspace/projects/hermes/demo_fuenf.txt", "content": "fuenf\n"}]}
        self.master.llm = Model(plan, [])
        self.bridge.fail_paths = {plan["files"][4]["path"]}  # Batch eins belegt, Batch zwei scheitert
        out = po.run(self.master, GOAL, self.session)
        self.assertEqual(out["status"], "PARTIAL")
        self.assertFalse(out["success"])
        self.assertEqual([s["status"] for s in out["plan"]], ["PASS"] * 4 + ["BLOCKED"])
        self.assertEqual(len(self.bridge.submits("create_file")), 5)

    def test_plan_derivation_failure_blocks_without_any_write(self):
        self.master.llm = Model("kein JSON")
        out = po.run(self.master, GOAL, self.session)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertIn("PLAN_NOT_DERIVED", out["data"].get("error") or out["reply"] + str(out))
        self.assertFalse(self.bridge.calls)
        self.assertFalse((self.root / "workspace" / "projects" / "hermes").exists())

    def test_unreadable_progress_fails_closed_instead_of_rederiving(self):
        self.run_order()
        state = self.state_files()[0]
        state.write_text("{", encoding="utf-8")
        self.master.llm = Model(PLAN3, [])
        out = po.run(self.master, GOAL, self.session)
        self.assertEqual(out["error"], "PROJECT_ORDER_STATE_UNREADABLE")
        self.assertFalse(self.master.llm.requests)
        self.assertEqual(len(self.bridge.submits("create_file")), 3)


class ProjectOrderRouteTests(unittest.TestCase):
    def setUp(self):
        self.master = SimpleNamespace()
        self.session = {}

    def test_goal_routes_to_project_order_with_named_targets(self):
        with patch("nexus.master.project_order.run", return_value={"success": True, "reply": "ok"}) as run:
            ev.route_work(self.master, GOAL, [], self.session)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["required_paths"], [])

    def test_multiple_named_files_route_to_project_order(self):
        with patch("nexus.master.project_order.run", return_value={"success": True, "reply": "ok"}) as run:
            ev.route_work(self.master, "Erstelle plan.md und status.json dafuer.", [], self.session)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["required_paths"], ["plan.md", "status.json"])

    def test_read_inputs_are_not_required_as_deliverables(self):
        goal = ("Lies die vorhandenen Dateien "
                "`workspace/projects/hermes/nexus_bestandsaufnahme.md` und "
                "`workspace/projects/hermes/nexus_arbeitsplan.md`. "
                "Werte die Aufgaben aus und speichere das Ergebnis im freigegebenen Projektbereich.")
        with patch("nexus.master.project_order.run", return_value={"success": True, "reply": "ok"}) as run:
            ev.route_work(self.master, goal, [], self.session)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["required_paths"], [])

    def test_only_created_files_are_required_deliverables(self):
        goal = ("Lies workspace/projects/hermes/plan.md und erstelle "
                "workspace/projects/hermes/status.md.")
        with patch("nexus.master.project_order.run", return_value={"success": True, "reply": "ok"}) as run:
            ev.route_work(self.master, goal, [], self.session)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["required_paths"],
                         ["workspace/projects/hermes/status.md"])

    def test_explicit_file_operation_never_becomes_a_project_order(self):
        with patch("nexus.master.project_order.run") as project, \
             patch("nexus.integrations.hermes_brain.HermesBrain.run",
                   return_value={"status": "BLOCKED", "task_ids": []}) as run:
            out = ev.route_work(self.master, 'Erstelle die Datei test.txt mit Inhalt: "exakt"', [], self.session)
        project.assert_not_called()
        self.assertEqual(run.call_args.kwargs["requirements"][0]["content"], "exakt")

    def test_privileged_goal_without_file_contract_stays_blocked(self):
        with patch("nexus.master.project_order.run") as project, \
             patch("nexus.master.decision.decide", return_value={"intent": "shell", "force_tools": True}):
            out = ev.route_work(self.master, "Führe das PowerShell-Skript aus.", [], self.session)
        project.assert_not_called()
        self.assertEqual(out["error"], "NO_VERIFIABLE_WORK_CONTRACT")

    def test_project_order_without_write_intent_is_not_executed(self):
        with patch("nexus.master.project_order.run") as project:
            ev.route_work(self.master, "Wie plant NEXUS ein Projekt?", [], self.session)
        project.assert_not_called()

    def test_project_order_resume_uses_stored_goal(self):
        self.session["hermes_order"] = {"task": GOAL, "requirements": [{"path": "workspace/projects/hermes/a.md"}],
                                        "project": True}
        with patch("nexus.master.project_order.run", return_value={"success": True, "reply": "ok"}) as run:
            ev.route_work(self.master, "weiter", [], self.session)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[1], GOAL)

    def test_privileged_steps_are_not_claimed_by_a_project_order(self):
        with patch("nexus.master.project_order.run", return_value={"success": True, "reply": "ok"}), \
             patch("nexus.master.decision.decide", return_value={"intent": "shell"}):
            out = ev.route_work(self.master, "Erstelle ein Projekt mit arbeitsplan und führe es aus", [], self.session)
        self.assertIn("privilegierte Aktionen", out["reply"])


if __name__ == "__main__":
    unittest.main()
