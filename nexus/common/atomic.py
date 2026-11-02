"""Atomare JSON-Zustandsdateien für gleichzeitige Schreiber (Threads und Prozesse).

- Eindeutige temporäre Datei je Schreibvorgang (PID + Zufall) im Zielverzeichnis:
  zwei Schreiber teilen sich nie dieselbe .tmp-Datei.
- flush + fsync vor os.replace; Ziel ist entweder alt oder vollständig neu.
- Windows: os.replace scheitert mit PermissionError (WinError 5/32), solange ein Leser
  oder Virenscanner das Ziel kurz geöffnet hält -> begrenzte Wiederholung, danach Fehler.
- file_lock(): prozessübergreifende Sperre (Sperrdatei) für Lesen-Ändern-Schreiben.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

REPLACE_ATTEMPTS = 20
LOCK_TIMEOUT_S = 15.0
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_HELD = threading.local()


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, target)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        with contextlib.suppress(OSError):
            if tmp.exists():
                tmp.unlink()


def atomic_write_json(path: Path | str, data, *, indent: int | None = 2) -> None:
    # Erst vollständig serialisieren: ein Fehler hinterlässt keine halbe Datei.
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))


def _thread_lock(key: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = _THREAD_LOCKS[key] = threading.RLock()
        return lock


@contextlib.contextmanager
def file_lock(path: Path | str, timeout: float = LOCK_TIMEOUT_S):
    """Exklusive Sperre für `path` über Threads und Prozesse hinweg (reentrant je Thread)."""
    target = Path(path)
    key = str(target.resolve()).lower()
    held = getattr(_HELD, "keys", None)
    if held is None:
        held = _HELD.keys = {}
    tlock = _thread_lock(key)
    if not tlock.acquire(timeout=max(0.0, timeout)):
        raise TimeoutError("STATE_LOCK_TIMEOUT: " + target.name)
    try:
        if held.get(key):
            held[key] += 1
            try:
                yield
            finally:
                held[key] -= 1
            return
        lock_file = target.with_name(target.name + ".lock")
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        stream = open(lock_file, "a+b")
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    _lock(stream)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("STATE_LOCK_TIMEOUT: " + target.name) from None
                    time.sleep(0.05)
            held[key] = 1
            try:
                yield
            finally:
                held.pop(key, None)
                with contextlib.suppress(OSError):
                    _unlock(stream)
        finally:
            stream.close()
    finally:
        tlock.release()


if os.name == "nt":
    import msvcrt

    def _lock(stream) -> None:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(stream) -> None:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(stream) -> None:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(stream) -> None:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
