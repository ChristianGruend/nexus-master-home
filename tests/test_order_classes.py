"""Auftragsklassen A-E: Frage, Dateiauftrag, Entwicklungsauftrag, nicht
autorisierte Aktion und Isolation zweier Auftraege/Sitzungen.

Prueft die tatsaechliche Engine-Route (route_work) und die nicht maskierte
Ursache der Koordination (collaboration.finish).
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nexus.master import evidence as ev
from nexus.master import project_order as po
from nexus.master import code_order as co
from nexus.master.controller import MasterController


GOAL = ("Erstelle im freigegebenen Hermes-Arbeitsbereich ein kleines Projekt mit Arbeitsplan, "
        "JSON-Statusdatei und Abschlussbericht. Plane die einzelnen Schritte selbst und verifiziere alle Ergebnisse.")

PLAN3 = {"files": [
    {"path": "workspace/projects/hermes/demo_arbeitsplan.md", "content": "# Arbeitsplan\n1. Schritt\n"},
    {"path": "workspace/projects/hermes/demo_status.json", "content": '{"status": "laufend"}\n'},
    {"path": "workspace/projects/hermes/demo_bericht.md", "content": "# Abschlussbericht\nGeprueft.\n"},
]}

SNAKE = "Baue mir ein Snake-Spiel in Python."


class Bridge:
    """Minimaler Bridge-Ersatz im Format des echten Task-Speichers."""

    def __init__(self, root):
        self.root = root
        self.calls = []
        self.records = {}

    def __call__(self, args):
        self.calls.append(copy.deepcopy(args))
        if args["operation"] == "status":
            return copy.deepcopy(self.records[args["task_id"]])
        task_id = "NX-20261011-" + str(len(self.records) + 1).zfill(4)
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
        directory = self.root / "workspace" / "bridge" / "tasks"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (record["id"] + ".json")).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")


class AQuestionWithoutExecution(unittest.TestCase):
    def setUp(self):
        self.master = SimpleNamespace()

    def test_question_executes_nothing_and_returns_to_chat(self):
        with patch("nexus.integrations.hermes_brain.HermesBrain.run") as run, \
             patch("nexus.master.project_order.run") as project, \
             patch("nexus.master.code_order.run") as code:
            out = ev.route_work(self.master, "Was ist ein Python-Skript?", [], {})
        run.assert_not_called(); project.assert_not_called(); code.assert_not_called()
        self.assertIsNone(out)


class BFileOrderWithReadback(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bridge = Bridge(self.root)
        self.patcher = patch("nexus.integrations.hermes_client.invoke", self.bridge)
        self.patcher.start()
        self.persist = patch.object(ev, "persisted_task", side_effect=self._persisted)
        self.persist.start()
        self.master = SimpleNamespace(project_fs=SimpleNamespace(root=self.temp.name), llm=None)

    def tearDown(self):
        self.persist.stop(); self.patcher.stop(); self.temp.cleanup()

    def _persisted(self, task_id, root=None):
        path = self.root / "workspace" / "bridge" / "tasks" / (str(task_id) + ".json")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) and value.get("id") == task_id else {}

    def test_three_file_order_is_written_and_read_back(self):
        from nexus.master.project_order import run as run_project_order
        self.master.llm = _PlanModel(PLAN3, _responses_for(PLAN3["files"]))
        out = run_project_order(self.master, GOAL, {}, required_paths=[])
        self.assertEqual(out["status"], "PASS")
        self.assertTrue(out["success"])
        self.assertEqual(len(out["data"]["task_ids"]), 6)  # 3x create + 3x read
        for req in PLAN3["files"]:
            self.assertEqual((self.root / req["path"]).read_text(encoding="utf-8"), req["content"])


class CSoftwareDevelopmentOrder(unittest.TestCase):
    def setUp(self):
        self.master = SimpleNamespace()

    def test_snake_order_routes_to_code_order_not_generic_block(self):
        sentinel = {"success": True, "status": "PASS", "task_ids": ["NX-x"], "data": {"order_type": "code_order"}}
        with patch("nexus.master.code_order.run", return_value=sentinel) as code:
            out = ev.route_work(self.master, SNAKE, [], {})
        self.assertEqual(code.call_count, 1)
        self.assertEqual(out["error"] if "error" in out else None, None)
        self.assertIs(out, sentinel)

    def test_plain_game_order_is_recognized_as_development(self):
        self.assertTrue(co.is_code_order(SNAKE))
        self.assertTrue(co.is_code_order("Baue ein Spiel."))
        self.assertTrue(co.is_code_order("Erstelle eine Webseite."))
        self.assertFalse(co.is_code_order("Erstelle eine Einkaufsliste."))
        self.assertFalse(co.is_code_order("Wie erstelle ich ein Python-Skript?"))

    def test_failed_development_plan_is_not_a_fake_pass(self):
        blocked = {"success": False, "status": "BLOCKED", "error": "CODE_PLAN_NOT_DERIVED:CODE_PLAN_PATH_NOT_AUTHORIZED",
                   "reply": "BLOCKED: Entwicklungsauftrag nicht ableitbar. Keine Datei geschrieben.", "data": {}}
        with patch("nexus.master.code_order.run", return_value=blocked):
            out = ev.route_work(self.master, SNAKE, [], {})
        self.assertFalse(out["success"])
        self.assertEqual(out["status"], "BLOCKED")
        self.assertIn("CODE_PLAN_NOT_DERIVED", out["error"])
        self.assertNotIn("PASS:", out["reply"])


class DUnauthorizedAction(unittest.TestCase):
    def setUp(self):
        self.master = SimpleNamespace()

    def test_write_outside_released_area_is_blocked_with_concrete_cause(self):
        with patch("nexus.integrations.hermes_brain.HermesBrain.run") as run:
            out = ev.route_work(self.master, 'Erstelle die Datei C:/Windows/secret.txt mit Inhalt: "x"', [], {})
        run.assert_not_called()
        self.assertFalse(out["success"])
        self.assertEqual(out["error"], "PATH_NOT_AUTHORIZED")

    def test_privileged_shell_goal_names_the_missing_capability(self):
        with patch("nexus.master.decision.decide", return_value={"intent": "shell", "force_tools": True}), \
             patch("nexus.integrations.hermes_brain.HermesBrain.run") as run:
            out = ev.route_work(self.master, "Führe das PowerShell-Skript aus.", [], {})
        run.assert_not_called()
        self.assertEqual(out["error"], "NO_VERIFIABLE_WORK_CONTRACT")
        self.assertIn("Shell", out["reply"])


class EIndependenceAndIsolation(unittest.TestCase):
    def test_new_valid_order_runs_after_a_blocked_order(self):
        master = SimpleNamespace()
        blocked = {"success": False, "status": "BLOCKED", "error": "CODE_PLAN_NOT_DERIVED",
                   "reply": "BLOCKED", "data": {}}
        with patch("nexus.master.code_order.run", return_value=blocked):
            first = ev.route_work(master, SNAKE, [], {})
        self.assertFalse(first["success"])
        ok = {"status": "PASS", "task_ids": ["NX-20261011-0001"], "success": True}
        with patch("nexus.integrations.hermes_brain.HermesBrain.run", return_value=ok) as run:
            second = ev.route_work(master, 'Erstelle die Datei workspace/projects/hermes/frei.md mit Inhalt: "frei"', [],
                                   {"hermes_order": {"task": "x", "requirements": []}})
        self.assertEqual(run.call_count, 1)
        self.assertNotEqual(second.get("error"), "CODE_PLAN_NOT_DERIVED")

    def test_two_sessions_do_not_share_the_task_state(self):
        controller = MasterController()
        controller._active_session_id = "sess-A"
        path_a = controller._task_state_path()
        controller._active_session_id = "sess-B"
        path_b = controller._task_state_path()
        self.assertNotEqual(path_a, path_b)
        self.assertNotEqual(path_a.parent / "nexus_active_task.json", path_a)


class FCollaborationSurfacesUpstreamCause(unittest.TestCase):
    def test_upstream_error_is_not_masked_by_no_execution_receipt(self):
        from nexus.chat import collaboration as coll

        class FakeRoom:
            def __init__(self, *a, **k):
                self.store = {}

            def collaboration(self, sid, value=None):
                if value is not None:
                    self.store[sid] = value
                return self.store.get(sid)

            def collaboration_run(self, sid, key, value=None):
                if value is not None:
                    self.store[(sid, key)] = value
                    self.store[sid] = value
                return self.store.get((sid, key))

            def append(self, **kw):
                return {"success": True, "message": {"message_id": "MSG-1", "content": kw.get("content")}}

        class FakeBrain:
            def __init__(self, master):
                self.master = master

            def converse(self, messages):
                return {"success": True, "reply": "ok", "model": "m"}

            def execute_collaboration(self, goal, session):
                return {"success": False, "error": "PLAN_NOT_DERIVED:UNSUPPORTED_ACTIONS", "reply": "BLOCKED",
                        "data": {"task_ids": [], "execution_status": "BLOCKED"}}

        master = SimpleNamespace(llm=SimpleNamespace(chat=lambda *a, **k: {"success": True, "reply": "ok", "model": "m"}))
        session = {"id": "sess-collab", "messages": []}
        with patch.object(coll, "SharedRoom", FakeRoom), \
             patch.object(coll, "HermesBrain", FakeBrain), \
             patch.object(coll, "verify_tasks", return_value={"ok": False, "error": "NO_EXECUTION_RECEIPT", "tasks": []}), \
             patch.object(coll, "_public", lambda value: value):
            out = coll.collaborate(master, "Erstelle ein Projekt mit Abschlussbericht", session)

        self.assertFalse(out["success"])
        self.assertEqual(out["status"], "BLOCKED")
        self.assertIn("PLAN_NOT_DERIVED:UNSUPPORTED_ACTIONS", out["error"])
        self.assertIn("NO_EXECUTION_RECEIPT", out["error"])


GAME = ("# NEXUS × HERMES – BAUT MIR EIN SPIEL!\n\nGemeinsam: NEXUS übernimmt die Koordination.\n\n"
        "Entwickelt ein tatsächlich spielbares Weltraumspiel für meinen PC.\n"
        "- Flüssige Animationen mit HTML5 Canvas und JavaScript.\n"
        "- Keine Cloud, keine kostenpflichtigen Dienste.\n"
        "- Falls das System HTML/JS nicht direkt ausführen oder in NEXUS integrieren darf, erstellt die "
        "Spielquellen innerhalb des erlaubten Bereichs.\n"
        "**WICHTIG:** Ich will ein echtes Spiel, keinen Bericht darüber, wie man eins bauen könnte. "
        "Keine erfundenen Tests, keine simulierten Funktionen und keine unnötigen Systemänderungen.")
PLAYER = ("gemeinsam: NEXUS / HERMES – AUFTRAG: VISUELLER MUSIKPLAYER FÜR NEXUS Baue in NEXUS Master Home "
          "einen internen visuellen Musikplayer, mit dem ich meine eigene lokale Musik abspielen kann.")


class GRealOrderRegressions(unittest.TestCase):
    """Echte, zuvor fehlgeleitete Aufträge aus der Chamber-Historie (11.10.2026)."""

    def setUp(self):
        self.master = SimpleNamespace()

    def test_game_subclause_is_not_a_pc_command_and_names_missing_file_types(self):
        self.assertIsNone(ev._exec_command_request(GAME))
        self.assertTrue(co.is_code_order(GAME))  # "keine simulierten Funktionen" verneint nicht den Bauakt
        sentinel = {"status": "PASS", "success": True, "reply": "WEB", "data": {}}
        with patch("nexus.master.code_order.run") as code, patch("nexus.master.project_order.run") as project,              patch("nexus.master.web_order.run", return_value=sentinel) as web:
            out = ev.route_work(self.master, GAME, [], {})
        code.assert_not_called(); project.assert_not_called()
        self.assertEqual(web.call_count, 1)  # HTML/JS-Spiel -> Web-Entwicklungspfad, nicht Python/Drei-Dateien
        self.assertIs(out, sentinel)

    def test_unsupported_toolchain_still_names_the_missing_capability(self):
        out = ev.route_work(self.master, "Entwickle eine Android-App in Kotlin als APK.", [], {})
        self.assertTrue(out["error"].startswith("CAPABILITY_MISSING:FILE_TYPE_NOT_RELEASED"))

    def test_change_to_nexus_itself_goes_to_the_approval_bound_patch_flow(self):
        sentinel = {"status": "PARTIAL", "error": "AWAITING_LOCAL_APPROVAL:AP-0000000000000000", "data": {}}
        with patch("nexus.master.code_order.run") as code, patch("nexus.master.web_order.run") as web,              patch("nexus.master.nexus_order.run", return_value=sentinel) as nexus:
            out = ev.route_work(self.master, PLAYER, [], {})
        code.assert_not_called(); web.assert_not_called()
        self.assertEqual(nexus.call_count, 1)
        self.assertIs(out, sentinel)

    def test_real_pc_command_is_still_recognized(self):
        self.assertEqual(ev._exec_command_request("Führe ipconfig aus."), "ipconfig")
        self.assertEqual(ev._exec_command_request("Bitte ping ausführen"), "ping")

    def test_single_described_file_is_planned_not_exact_content_blocked(self):
        text = ("Erstelle die Datei workspace/projects/hermes/nexus_musikplayer_anleitung.md und beschreibe "
                "darin kurz, wie man den vorhandenen Musikplayer öffnet und welche Funktionen er hat.")
        with patch("nexus.master.project_order.run", return_value={"status": "PASS", "success": True}) as project:
            ev.route_work(self.master, text, [], {})
        self.assertEqual(project.call_args.kwargs["required_paths"],
                         ["workspace/projects/hermes/nexus_musikplayer_anleitung.md"])

    def test_python_game_stays_an_allowed_development_order(self):
        with patch("nexus.master.code_order.run", return_value={"status": "PARTIAL"}) as code:
            ev.route_work(self.master, SNAKE, [], {})
        self.assertEqual(code.call_count, 1)

    def test_negated_or_conditional_tech_mentions_do_not_block(self):
        self.assertEqual(ev.capability_gaps("Baue ein Python-Spiel, keine Webseite und ohne JavaScript."), [])


class INoRealOrderStateFromTests(unittest.TestCase):
    """Schutz: Klassifikationstests dürfen keine echten Auftragszustände anlegen."""

    def test_routing_tests_leave_no_order_state(self):
        state = Path(__file__).resolve().parents[1] / "workspace" / "state"
        before = {p.name for p in state.glob("hermes-*-order-*.json")} if state.is_dir() else set()
        with patch("nexus.master.web_order.run", return_value={"status": "PASS", "data": {}}),              patch("nexus.master.nexus_order.run", return_value={"status": "PASS", "data": {}}),              patch("nexus.master.code_order.run", return_value={"status": "PASS", "data": {}}),              patch("nexus.master.project_order.run", return_value={"status": "PASS", "data": {}}):
            for text in (GAME, PLAYER, SNAKE, "Erstellt eine neue Funktion für NEXUS."):
                ev.route_work(SimpleNamespace(), text, [], {})
        after = {p.name for p in state.glob("hermes-*-order-*.json")} if state.is_dir() else set()
        self.assertEqual(after - before, set())


class HStateIsolation(unittest.TestCase):
    def test_two_collaboration_orders_in_one_session_keep_separate_states(self):
        from nexus.chat.shared_room import SharedRoom
        with tempfile.TemporaryDirectory() as tmp, \
             patch("nexus.chat.shared_room.ROOM_DIR", Path(tmp), create=True):
            room = SharedRoom()
            if not hasattr(room, "load"):
                self.skipTest("SharedRoom ohne load")
            store = {}
            with patch.object(SharedRoom, "load", lambda self, sid: store.setdefault(sid, {})), \
                 patch.object(SharedRoom, "_save", lambda self, r: None):
                room.collaboration_run("S", "k1", {"key": "k1", "status": "BLOCKED", "run_id": "COL-1"})
                room.collaboration_run("S", "k2", {"key": "k2", "status": "PASS", "run_id": "COL-2"})
                self.assertEqual(room.collaboration_run("S", "k1")["run_id"], "COL-1")
                self.assertEqual(room.collaboration_run("S", "k2")["run_id"], "COL-2")
                self.assertEqual(room.collaboration("S")["run_id"], "COL-2")  # nur Zeiger fuer die UI

    def test_same_order_running_twice_is_refused_other_order_is_not_blocked(self):
        from nexus.integrations.hermes_brain import HermesBrain
        with tempfile.TemporaryDirectory() as tmp:
            master = SimpleNamespace(project_fs=SimpleNamespace(root=tmp), llm=None)
            brain = HermesBrain(master, llm=SimpleNamespace(chat=lambda *a, **k: {"success": False, "error": "x"}))
            req = [{"path": "workspace/projects/hermes/a.md", "content": "a"}]
            fingerprint = __import__("hashlib").sha256(json.dumps(["T", req], sort_keys=True,
                                                                   ensure_ascii=False).encode()).hexdigest()
            state_dir = Path(tmp) / "workspace" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            lock = state_dir / ("hermes-order-" + fingerprint + ".lock")
            with open(lock, "a+b") as held:
                held.seek(0)
                import msvcrt
                msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
                try:
                    busy = brain.run("T", requirements=req)
                    other = brain.run("U", requirements=req)
                finally:
                    held.seek(0); msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
            self.assertEqual(busy["error"], "ORDER_ALREADY_RUNNING")
            self.assertNotEqual(other["error"], "ORDER_ALREADY_RUNNING")
            self.assertFalse(str(other["error"]).startswith("STATE_OR_LOCK_ERROR"))


def _answer(name=None, args=None):
    calls = [] if name is None else [{"function": {"name": name, "arguments": args}}]
    return {"success": True, "model": "offline-test", "reply": "done" if not calls else "",
            "message": {"role": "assistant", "content": "done" if not calls else "", "tool_calls": calls},
            "tool_calls": calls}


def _responses_for(requirements):
    out = []
    for index in range(0, len(requirements), 4):
        chunk = requirements[index:index + 4]
        calls = [_answer("create_file", r)["tool_calls"][0] for r in chunk]
        calls += [_answer("read_file", {"path": r["path"]})["tool_calls"][0] for r in chunk]
        response = _answer()
        response["tool_calls"] = calls
        response["message"] = {"role": "assistant", "content": "", "tool_calls": calls}
        out.append(response)
    return out


class _PlanModel:
    def __init__(self, plan, responses=None):
        self.plan = plan
        self.responses = list(responses or [])

    def chat(self, messages, **kwargs):
        if kwargs.get("tools"):
            return self.responses.pop(0) if self.responses else _answer()
        return {"success": True, "model": "offline-test",
                "reply": self.plan if isinstance(self.plan, str) else json.dumps(self.plan)}


if __name__ == "__main__":
    unittest.main()
