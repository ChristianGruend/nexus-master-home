"""Hermes-Entwicklungsfähigkeiten: Sandbox, Browserprüfung, Vorschau, NEXUS-Patches, Routing."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nexus.bridge import dev_executors as dx
from nexus.bridge.errors import BridgeError
from nexus.bridge.web_check import _criteria, _remap
from nexus.master import web_order as wo
from nexus.master.nexus_order import is_nexus_change
from nexus.integrations.hermes_client import validate_task, IntegrationError


def _sample(t, h, frames, distinct=6, game=None):
    game = game if game is not None else {"enemies": 2, "enemies_visible": 2, "lasers": 1, "score": 0, "lives": 3,
                                          "over": False, "px": 480, "py": 500}
    return {"t": t, "frames": frames, "canvas": {"w": 960, "h": 600, "hash": h, "distinct": distinct}, "game": game}


class ProtectedNexusPaths(unittest.TestCase):
    def test_security_core_is_never_patchable(self):
        for path in ("nexus/bridge/auth.py", "nexus/bridge/approvals.py", "nexus/bridge/permissions.py",
                     "nexus/bridge/appcontainer.py", "nexus/integrations/hermes_client.py",
                     "nexus/chat/static/approvals.html", "nexus/bridge_gateway/x.py", "workspace/bridge/.token",
                     "nexus/../run_server.py", "nexus/common/token_store.py"):
            self.assertIsNone(dx.nexus_target_allowed(path), path)

    def test_ordinary_ui_file_is_patchable(self):
        self.assertEqual(dx.nexus_target_allowed("nexus/chat/static/chamber/js/timeline.js"),
                         "nexus/chat/static/chamber/js/timeline.js")

    def test_client_contract_rejects_protected_patch_and_bad_ids(self):
        with self.assertRaises(IntegrationError):
            validate_task("hermes_prepare_nexus_patch", {"patch_id": "PATCH-0123456789ab", "goal": "x",
                                                         "files": [{"path": "nexus/bridge/auth.py", "content": "x"}]})
        with self.assertRaises(IntegrationError):
            validate_task("nexus_apply_patch", {"patch_id": "../etc"})
        validate_task("nexus_apply_patch", {"patch_id": "PATCH-0123456789ab", "approval_id": "AP-0123456789abcdef"})


class ApprovalBinding(unittest.TestCase):
    def test_apply_refuses_an_approval_granted_for_another_patch(self):
        rec = SimpleNamespace(id="NX-20261011-9999", approval_id="AP-0123456789abcdef")
        other = SimpleNamespace(params={"patch_id": "PATCH-aaaaaaaaaaaa"})
        with patch("nexus.bridge.approvals.approvals", return_value=SimpleNamespace(get=lambda _id: other)):
            with self.assertRaises(BridgeError):
                dx.exec_nexus_apply_patch({"patch_id": "PATCH-bbbbbbbbbbbb"}, rec)


class WebCriteria(unittest.TestCase):
    def test_playable_canvas_passes_only_with_all_criteria(self):
        report = {"errors": [], "key_listeners": 2, "keys_sent": 11, "net": "BLOCKED",
                  "samples": [_sample("t1000", 1, 50), _sample("t1600", 2, 80), _sample("t4800", 3, 250),
                              _sample("t6800", 4, 370)]}
        idle = {"samples": [_sample("t4800", 33, 250), _sample("t6800", 44, 370)]}
        report["scenarios"] = {"hit": {"passed": True}, "move": {"passed": True}, "crash": {"passed": True}}
        crit = _criteria(report, [{"file": "game.js", "ok": True}], idle)
        self.assertTrue(all(crit.values()), crit)

    def test_flicker_without_enemies_or_with_runaway_player_is_not_a_game(self):
        runaway = {"enemies": 0, "lasers": 3, "score": 0, "lives": 3, "over": False, "px": 71822, "py": -40826}
        report = {"errors": [], "key_listeners": 2, "keys_sent": 11, "net": "BLOCKED",
                  "samples": [_sample(t, i, 50 * (i + 1), game=runaway) for i, t in
                              enumerate(("t1000", "t1600", "t3400", "t4800", "t6800"))]}
        report["scenarios"] = {"hit": {"passed": False}, "move": {"passed": False}, "crash": {"passed": False}}
        crit = _criteria(report, [], {"samples": []})
        self.assertFalse(crit["enemies_appear"])
        self.assertFalse(crit["player_stays_in_view"])
        self.assertFalse(crit["laser_hits_enemy"])
        self.assertFalse(crit["movement_stops_on_release"])
        self.assertFalse(crit["enemy_collision_costs_life"])

    def test_tidy_collapses_patch_artifacts_but_keeps_intended_pairs(self):
        self.assertEqual(wo.tidy("a\nx++;\nx++;\nc\nc\nc\nd\n"), "a\nx++;\nx++;\nc\nd\n")

    def test_static_or_input_ignoring_page_fails(self):
        report = {"errors": [], "key_listeners": 1, "keys_sent": 11, "net": "BLOCKED",
                  "samples": [_sample("t1000", 1, 50), _sample("t1600", 1, 80), _sample("t4800", 3, 250),
                              _sample("t6800", 4, 370)]}
        idle = {"samples": [_sample("t4800", 3, 250), _sample("t6800", 4, 370)]}
        crit = _criteria(report, [], idle)
        self.assertFalse(crit["animates"])
        self.assertFalse(crit["input_changes_game"])

    def test_inline_line_numbers_map_back_to_source_file(self):
        offsets = {"game.js": 15, "__ranges__": [(15, 200, "game.js")]}
        self.assertEqual(_remap(["X @game.js:255:3", "Y @index.html:20:1"], offsets),
                         ["X @game.js:241:3", "Y @game.js:6:1"])


class DeterministicRepairs(unittest.TestCase):
    def test_const_reassignment_becomes_let_at_nearest_declaration(self):
        src = "const a = 1;\nfunction f(){\n  const { b, c } = s;\n  b = 2;\n}\n"
        out, steps = wo.deterministic_repair(src, ["Uncaught TypeError: Assignment to constant variable. @game.js:4:5"])
        self.assertIn("let { b, c } = s;", out)
        self.assertTrue(steps)

    def test_destructured_name_missing_in_other_function_is_added(self):
        src = "function update(){\n  let { stars, ship } = state;\n}\nfunction draw() {\n  for (const s of stars) {}\n}\n"
        out, steps = wo.deterministic_repair(src, ["Uncaught ReferenceError: stars is not defined @game.js:5:19"])
        self.assertIn("function draw() {\n    let { stars, ship } = state;", out)
        self.assertTrue(steps)

    def test_unrelated_error_is_left_to_hermes(self):
        out, steps = wo.deterministic_repair("x();\n", ["Uncaught TypeError: x is not a function @game.js:1:1"])
        self.assertEqual((out, steps), ("x();\n", []))


class WebOrderLoopIsBounded(unittest.TestCase):
    def test_passed_check_is_not_rechecked_forever(self):
        import json, tempfile
        from pathlib import Path
        from nexus.bridge.web_check import CRITERIA_VERSION
        with tempfile.TemporaryDirectory() as tmp:
            goal = "Baut ein Spiel."
            path = wo._state_path(Path(tmp), goal)
            base = Path(tmp) / wo.CODE_DIR / "spiel"
            base.mkdir(parents=True)
            (base / "game.js").write_text("x", encoding="utf-8")
            old = {"passed": True, "task_id": "NX-20261011-0001", "criteria": {"a": True}}  # ohne Version
            path.write_text(json.dumps({"schema": wo.SCHEMA, "goal": goal, "plan_id": "WEB-x", "status": "PASS",
                "model_calls": 0, "plan": {"slug": "spiel", "title": "S", "controls": "", "features": [],
                "concept": ""}, "files": {"game.js": "x"}, "writes": {"status": "PASS", "task_ids": []},
                "rounds": [{"round": 0, "patch": None, "check": old}], "publish": None}), encoding="utf-8")
            calls = []
            new = {"task_id": "NX-20261011-0002", "success": True, "passed": True, "criteria": {"a": True},
                   "criteria_version": CRITERIA_VERSION}
            master = SimpleNamespace(project_fs=SimpleNamespace(root=tmp), llm=None)
            with patch.object(wo, "_check", side_effect=lambda e: calls.append(e) or dict(new)), \
                 patch("nexus.integrations.hermes_client.invoke",
                       return_value={"success": True, "task_id": "NX-20261011-0003",
                                     "result": {"published": True, "url": "/preview/spiel/index.html"}}):
                out = wo.run(master, goal, {})
            self.assertEqual(len(calls), 1)  # genau eine Neuprüfung nach Versionswechsel
            self.assertEqual(out["status"], "PASS")


class Routing(unittest.TestCase):
    def test_order_classes(self):
        self.assertTrue(wo.is_web_order("Baut mir ein spielbares Weltraumspiel."))
        self.assertTrue(wo.is_web_order("NEXUS und Hermes, entwickelt gemeinsam das spielbare Browsergame NEXUS ORBITAL DEFENDER."))
        self.assertFalse(wo.is_web_order("Baue mir ein Snake-Spiel in Python."))
        self.assertFalse(wo.is_web_order("Wie baue ich ein Spiel?"))
        self.assertFalse(wo.is_web_order("Baue keine Webseite."))
        self.assertTrue(is_nexus_change("Verbessert eure Benutzeroberfläche."))
        self.assertTrue(is_nexus_change("Erstellt eine neue Funktion für NEXUS."))
        self.assertFalse(is_nexus_change("Falls das System das nicht in NEXUS integrieren darf, erstellt die Quellen."))


@unittest.skipUnless(os.name == "nt", "AppContainer nur unter Windows")
class AppContainerIsolation(unittest.TestCase):
    def test_breakout_attempts_are_denied(self):
        from nexus.bridge.appcontainer import self_test
        result = self_test(force=True)
        self.assertTrue(result.get("ok"), result)
        for key in ("read_secret", "read_user_profile", "read_nexus_source", "write_outside",
                    "net_loopback_bridge", "net_external"):
            self.assertTrue(str(result["checks"][key]).startswith("DENIED"), key)
        self.assertEqual(result["checks"]["memory_limit"], "ENFORCED")
        self.assertEqual(result["checks"]["time_limit"], "ENFORCED")


if __name__ == "__main__":
    unittest.main()
