"""Regression: bridge task records load with and without a UTF-8 BOM."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nexus.bridge.models import TaskRecord
from nexus.bridge.storage import TaskStorage

TS = "2026-10-24T10:05:20+02:00"
TASK_ID = "NX-20261024-0003"


def _record(status: str = "succeeded") -> TaskRecord:
    return TaskRecord(
        id=TASK_ID,
        task="restart_nexus_bridge_exact",
        status=status,
        permission="SAFE_WRITE",
        created_at=TS,
        updated_at=TS,
        params={"target": "bridge", "note": "Grüße aus Köln – geprüft"},
        result={"success": True, "old_pid": 21856, "new_pid": 6160} if status == "succeeded" else None,
        error="Synthetischer Fehler: Übergabe fehlgeschlagen" if status == "failed" else None,
        error_code="SYNTHETIC_TEST_ERROR" if status == "failed" else None,
        log=[f"{TS} status={status}", "UTF-8: äöüß"],
        source="task_engine_repair_20261024",
        client="codex",
        session_id="synthetic-bom-session",
        approval_id="AP-03302abfcbd5c4f3",
        execution={"instance_id": "synthetic-bom-instance", "started_at": TS},
    )


class BridgeStorageBomTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nx_bom_")
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, encoding: str, status: str) -> Path:
        fp = self.dir / f"{TASK_ID}.json"
        text = json.dumps(
            _record(status).to_dict(include_log=True),
            ensure_ascii=False,
            indent=2,
        )
        fp.write_text(text, encoding=encoding, newline="\n")
        return fp

    def _assert_record(self, rec: TaskRecord | None, status: str) -> None:
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertEqual(rec.to_dict(include_log=True), _record(status).to_dict(include_log=True))

    def test_reads_json_without_bom(self) -> None:
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                fp = self._write("utf-8", status)
                before = fp.read_bytes()
                self.assertFalse(before.startswith(b"\xef\xbb\xbf"))

                self._assert_record(TaskStorage(self.dir).get(TASK_ID), status)
                self.assertEqual(fp.read_bytes(), before, "Task record must remain unchanged")

    def test_reads_json_with_bom_without_changing_file(self) -> None:
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                fp = self._write("utf-8-sig", status)
                before = fp.read_bytes()
                self.assertTrue(before.startswith(b"\xef\xbb\xbf"))

                self._assert_record(TaskStorage(self.dir).get(TASK_ID), status)
                self.assertEqual(fp.read_bytes(), before, "BOM task record must remain unchanged")


if __name__ == "__main__":
    unittest.main()
