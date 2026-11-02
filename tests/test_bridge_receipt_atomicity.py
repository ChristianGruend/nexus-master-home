"""P2: Atomare Speicherung der Bridge-Task-Nachweise verifizieren.

Die Bridge-Belege sind die Grundlage jeder spaeteren Erfolgspruefung.
Nachweisziel:
  1. Vollstaendiger Inhalt wird erst nach fsync per os.replace sichtbar.
  2. Ein Fehlschlag beim Schreiben laesst den vorherigen Beleg unangetastet.
  3. Leser sehen nie einen abgeschnittenen Beleg, auch nicht mit Crash-Rest.
  4. Nach erfolgreichem Persistieren bleibt kein .tmp-Rest liegen.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nexus.bridge.storage as st
from nexus.bridge.models import TaskRecord
from nexus.bridge.storage import TaskStorage

TS = "2026-10-09T12:00:00+02:00"
TASK_ID = "NX-20261009-0900"


def _record(status: str = "running", result=None) -> TaskRecord:
    return TaskRecord(
        id=TASK_ID,
        task="create_file",
        status=status,
        permission="SAFE_WRITE",
        created_at=TS,
        updated_at=TS,
        params={"path": "workspace/projects/hermes/atomic.txt", "content": "x"},
        result=result,
    )


class _ProbeOS:
    """Delegiert an das echte os-Modul und protokolliert fsync/replace."""

    def __init__(self, events: list) -> None:
        self._events = events
        self.on_replace = None

    def __getattr__(self, name):
        return getattr(os, name)

    def fsync(self, fd: int) -> None:
        self._events.append("fsync")
        os.fsync(fd)

    def replace(self, src, dst) -> None:
        self._events.append("replace")
        if self.on_replace:
            self.on_replace(src, dst)
        os.replace(src, dst)


class AtomicReceiptStorage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="nx_atomic_")
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.final = self.dir / f"{TASK_ID}.json"
        self.tmp_file = self.dir / f"{TASK_ID}.tmp"

    def test_publish_only_after_fsync_and_only_complete_json(self):
        events: list[str] = []
        probe = _ProbeOS(events)
        seen: dict = {}

        def inspect(src, dst):
            # Quelle ist bereits vollstaendig und PARSBAR, Ziel ist noch alt/fehlt.
            seen["src"] = src
            seen["dst"] = dst
            seen["parsed"] = json.loads(Path(src).read_text(encoding="utf-8"))
            seen["dst_existed"] = Path(dst).exists()

        probe.on_replace = inspect
        with patch.object(st, "os", probe):
            TaskStorage(self.dir).put(_record("running"))

        self.assertEqual(events, ["fsync", "replace"], "fsync muss vor dem Publish liegen")
        self.assertEqual(Path(seen["src"]).name, f"{TASK_ID}.tmp", "geschrieben wird ins tmp")
        self.assertEqual(Path(seen["dst"]).resolve(), self.final.resolve())
        self.assertFalse(seen["dst_existed"], "Ziel darf erst nach vollstaendigem tmp existieren")
        self.assertEqual(seen["parsed"]["id"], TASK_ID)
        self.assertEqual(json.loads(self.final.read_text(encoding="utf-8"))["status"], "running")
        self.assertEqual(list(self.dir.glob("*.tmp")), [], "kein tmp-Rest nach Erfolg")

    def test_failed_write_keeps_previous_receipt_intact(self):
        TaskStorage(self.dir).put(_record("running"))
        before = self.final.read_bytes()

        poisoned = _record("succeeded")
        poisoned.result = {"x": object()}  # json.dump wirft TypeError
        with self.assertRaises(TypeError):
            TaskStorage(self.dir).put(poisoned)

        self.assertEqual(self.final.read_bytes(), before, "alter Beleg bleibt bytegenau")
        reloaded = TaskStorage(self.dir).get(TASK_ID)
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.status, "running", "kein halber/neuer Beleg sichtbar")

    def test_reader_never_sees_partial_json_even_with_crash_leftover(self):
        TaskStorage(self.dir).put(_record("running"))
        # Absturz-Rest: unvollstaendige .tmp direkt neben dem gueltigen Beleg.
        self.tmp_file.write_text(
            '{"id": "NX-20261009-0900", "status": "succ', encoding="utf-8"
        )

        reloaded = TaskStorage(self.dir).get(TASK_ID)
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.status, "running", "Reader ignorieren .tmp-Reste")

        # Der naechste saubere Vorgang ueberschreibt den Rest atomar.
        TaskStorage(self.dir).put(_record("succeeded"))
        self.assertEqual(json.loads(self.final.read_text(encoding="utf-8"))["status"], "succeeded")
        self.assertEqual(list(self.dir.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
