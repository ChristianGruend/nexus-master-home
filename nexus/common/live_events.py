"""Live-Event-Bus: zentrale JSONL-Spur aller NEXUS/Hermes/Crew-Aktionen + Auto-Open Monitorfenster.

Datei: workspace/audit/live_events.jsonl (Rotation bei 5 MB -> .1).
Nie Exceptions nach aussen; Secrets werden grob maskiert.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from nexus.common.paths import ROOT

LIVE_FILE = ROOT / "workspace" / "audit" / "live_events.jsonl"
MON_DIR = ROOT / "workspace" / "live_monitor"
PID_FILE = MON_DIR / "window.pid"
MAX_BYTES = 5 * 1024 * 1024
_LOCK = threading.Lock()
_OPEN_LOCK = threading.Lock()
_last_open_try = 0.0
_SECRET = re.compile(r"(?i)(token|password|passwort|secret|bearer|api[_-]?key)([\"'\s:=]+)([^\s\"',}]{4,})")


def _mask(text: str) -> str:
    return _SECRET.sub(lambda m: m.group(1) + m.group(2) + "***", text)


def _short(value, limit: int = 600) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            value = str(value)
    value = _mask(value)
    return value if len(value) <= limit else value[:limit] + " …"


def emit(agent: str, kind: str, text, *, target: str | None = None, ok=None,
         data=None, auto_open: bool = True) -> dict | None:
    """Ein Event anhaengen. agent: NEXUS/Hermes/Crew/Fixer/Bridge/Hermes-PS."""
    if "pytest" in sys.modules and not os.environ.get("NEXUS_LIVE_FORCE"):
        return None
    try:
        rec = {"ts": datetime.now().isoformat(timespec="milliseconds"), "agent": str(agent)[:40],
               "kind": str(kind)[:40], "text": _short(text)}
        if target:
            rec["target"] = str(target)[:40]
        if ok is not None:
            rec["ok"] = bool(ok)
        if data is not None:
            rec["data"] = _short(data, 1500)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _LOCK:
            LIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
            try:
                if LIVE_FILE.stat().st_size > MAX_BYTES:
                    os.replace(LIVE_FILE, LIVE_FILE.with_suffix(".jsonl.1"))
            except FileNotFoundError:
                pass
            with LIVE_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
        if auto_open:
            ensure_monitor()
        return rec
    except Exception:
        return None


def tail(limit: int = 200) -> list[dict]:
    try:
        lines = LIVE_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(limit, 2000)):]
    except FileNotFoundError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------- Auto-Open
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


def _browser() -> str | None:
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if Path(p).is_file():
            return p
    return None


def monitor_open() -> bool:
    try:
        return _pid_alive(int(PID_FILE.read_text().strip()))
    except Exception:
        return False


def ensure_monitor(force: bool = False) -> dict:
    """Oeffnet das Monitorfenster genau einmal (PID-Datei), max. 1 Versuch / 20 s."""
    global _last_open_try
    if not force and (os.environ.get("NEXUS_LIVE_AUTOOPEN", "1") == "0" or "pytest" in sys.modules):
        return {"opened": False, "reason": "disabled"}
    with _OPEN_LOCK:
        if monitor_open():
            return {"opened": False, "reason": "already_open"}
        now = time.time()
        if not force and now - _last_open_try < 20:
            return {"opened": False, "reason": "throttled"}
        _last_open_try = now
        exe = _browser()
        if not exe:
            return {"opened": False, "reason": "no_browser"}
        from nexus.common.config import Config
        url = f"http://127.0.0.1:{Config.PORT}/live"
        MON_DIR.mkdir(parents=True, exist_ok=True)
        prof = MON_DIR / "browser_profile"  # Profil auf G:, nichts Neues auf C:
        args = [exe, f"--app={url}", f"--user-data-dir={prof}", "--window-size=760,900",
                "--window-position=1100,40", "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--disable-sync", "--disk-cache-size=10000000"]
        try:
            flags = 0x00004000 if os.name == "nt" else 0  # BELOW_NORMAL_PRIORITY_CLASS
            p = subprocess.Popen(args, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            PID_FILE.write_text(str(p.pid))
            emit("Monitor", "window", f"Monitorfenster geoeffnet (PID {p.pid})", ok=True, auto_open=False)
            return {"opened": True, "pid": p.pid}
        except Exception as exc:
            return {"opened": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}