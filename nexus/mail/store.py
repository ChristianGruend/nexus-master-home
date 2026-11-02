"""NEXUS Mail – lokaler Speicher (Geruest 29.10.2026, MAIL01/MAIL03).

SQLite unter workspace/mail/nexus_mail.db. Inhaltsfelder werden per Windows-DPAPI
(win32crypt, benutzergebunden) verschluesselt gespeichert. Keine Zugangsdaten hier:
Passwoerter gehoeren ausschliesslich in den Windows-Anmeldeinformationsspeicher.
"""
from __future__ import annotations

import base64
import sqlite3
import time
from pathlib import Path
from typing import Any

from nexus.common.paths import WORKSPACE
from win32crypt import CryptProtectData

DB_PATH = WORKSPACE / "mail" / "nexus_mail.db"
FOLDERS = ("inbox", "sent", "drafts", "archive", "trash")
_ENC_FIELDS = ("sender", "recipients", "subject", "body")


def _protect(text: str) -> str:
    # Codiert den Text unter Verwendung von Windows-CryptoAPI für lokalen Schutz im Anmeldeinformationspeicher.
    # Falls text leer ist, wird eine Leerzeichen-Sequenz codiert.
    try:
        data_to_encode = (text or " ").encode("utf-8")
        blob = CryptProtectData(data_to_encode, "nexus-mail", None, None, None, 0)
        encoded_blob = base64.b64encode(blob).decode("ascii")
        return "dp1:" + encoded_blob
    except Exception as e:
        # Bei Fehlern beim Codieren (z.B. wenn win32crypt nicht verfügbar ist) wird ein Standard-Error-Wert zurückgegeben,
        # der keine geheimen Daten preisgibt und den Fehlerzustand anzeigt.
        raise ValueError(f"Nexus Mail Protection Error: Verschlüsselung fehlgeschlagen.") from e


def _unprotect(value: str) -> str:
    if not value or not value.startswith("dp1:"):
        return value or ""
    import win32crypt
    _desc, data = win32crypt.CryptUnprotectData(base64.b64decode(value[4:]), None, None, None, 0)
    return data.decode("utf-8")


class MailStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, folder TEXT NOT NULL,"
                       " sender TEXT, recipients TEXT, subject TEXT, body TEXT, ts REAL NOT NULL, unread INTEGER DEFAULT 1)")
            db.execute("CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT)")

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path))

    def add(self, folder: str, sender: str, recipients: str, subject: str, body: str, ts: float | None = None) -> int:
        # Schutz: Keine Passwoerter oder sensible Daten im Sender/Inhalt (heuristische Pruefung)
        if any(kw in (sender.lower() or "", body.lower() or "") for kw in ["password", "pwd", "login"]):
            raise ValueError("PROTECTED_DATA_DETECTED: Sensible Daten nicht speicherbar.")
        if folder not in FOLDERS:
            raise ValueError("UNKNOWN_FOLDER")
        vals = [_protect(v) for v in (sender, recipients, subject, body)]
        with self._db() as db:
            cur = db.execute("INSERT INTO messages (folder, sender, recipients, subject, body, ts) VALUES (?,?,?,?,?,?)",
                             (folder, *vals, float(ts or time.time())))
            return int(cur.lastrowid)

    def _row(self, r: tuple) -> dict[str, Any]:
        d = {"id": r[0], "folder": r[1], "ts": r[6], "unread": bool(r[7])}
        for k, v in zip(_ENC_FIELDS, r[2:6]):
            d[k] = _unprotect(v)
        return d

    def list(self, folder: str = "inbox") -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT id, folder, sender, recipients, subject, body, ts, unread FROM messages"
                              " WHERE folder=? ORDER BY ts DESC", (folder,)).fetchall()
        return [self._row(r) for r in rows]

    def get(self, mid: int) -> dict[str, Any] | None:
        with self._db() as db:
            r = db.execute("SELECT id, folder, sender, recipients, subject, body, ts, unread FROM messages WHERE id=?",
                           (int(mid),)).fetchone()
        return self._row(r) if r else None

    def move(self, mid: int, folder: str) -> bool:
        if folder not in FOLDERS:
            raise ValueError("UNKNOWN_FOLDER")
        with self._db() as db:
            return db.execute("UPDATE messages SET folder=? WHERE id=?", (folder, int(mid))).rowcount == 1

    def search(self, query: str) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        out = []
        for f in FOLDERS:
            for m in self.list(f):
                if q and any(q in (m.get(k) or "").lower() for k in _ENC_FIELDS):
                    out.append(m)
        return out
