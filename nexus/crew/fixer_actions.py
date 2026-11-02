"""@Fixer: sichere Selbst-Fixes nach fester Whitelist (Freigabe Eddie: "selbst beheben").

Erlaubt: Stale-Startfehler nach Health-Pruefung quittieren, alte Logs auf G: zippen/aufraeumen,
Ollama-Modelle entladen (RAM), NEXUS headless neu starten (nur wenn Health down).
Verboten: alles auf C:, Handy/NEXUS Mobile, Windows-Systemeinstellungen, bezahlte Dienste,
Code-Patches (die laufen nur ueber den Selbstbau-Weg mit Backup/Tests/Rollback/Audit).
Jede Aktion wird nachgeprueft und ehrlich als PASS/FAIL gemeldet.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from nexus.common.paths import WORKSPACE

ALLOWED = ("ack_stale", "rotate_logs", "unload_ollama", "restart_nexus")
HEALTH = "http://127.0.0.1:8299/api/health"
OLLAMA = "http://127.0.0.1:11434"
TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
START_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\] START local verified")


def _safe(p: Path) -> Path:
    r = Path(p).resolve()
    ws = Path(WORKSPACE).resolve()
    if r.drive.upper() != "G:":
        raise PermissionError(f"nur G: erlaubt: {r}")
    if ws.drive.upper() == "G:" and ws not in r.parents and r != ws and "nexus_fixer_test" not in str(r):
        raise PermissionError(f"ausserhalb workspace: {r}")
    return r


def _ack_file(ws: Path) -> Path:
    return ws / "state" / "fixer_ack.json"


def acked_until(ws: Path | None = None) -> str:
    try:
        return json.loads(_ack_file(ws or WORKSPACE).read_text(encoding="utf-8")).get("acked_until", "")
    except Exception:
        return ""


def is_acked(line: str, ws: Path | None = None) -> bool:
    m = TS_RE.search(line)
    a = acked_until(ws)
    return bool(m and a and m.group(1) <= a)


def health_ok(timeout: float = 4) -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _audit(ws: Path, rec: dict) -> None:
    p = _safe(ws / "audit" / "fixer_actions.jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **rec}, ensure_ascii=False) + "\n")
    try:  # Live-Monitor (20261028)
        from nexus.common.live_events import emit as _emit
        _emit("Fixer", "aktion", rec.get("action") or rec.get("text") or "fixer", ok=rec.get("ok"), data=rec)
    except Exception:
        pass


def plan(errors: list[str], ws: Path | None = None) -> list[str]:
    ws = ws or WORKSPACE
    acts: list[str] = []
    st = last_verified_start(ws)
    if st and any((m := TS_RE.search(e)) and m.group(1) < st for e in errors):
        acts.append("ack_stale")
    old = [p for p in (ws / "logs").glob("ollama-*.log") if time.time() - p.stat().st_mtime > 3 * 86400]
    if old:
        acts.append("rotate_logs")
    if _ram_free_pct() < 15:
        acts.append("unload_ollama")
    if not health_ok():
        acts.append("restart_nexus")
    return [a for a in acts if a in ALLOWED]


def _ram_free_pct() -> float:
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tot", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                        ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
        m = MS(); m.l = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return 100.0 * m.avail / m.tot
    except Exception:
        return 100.0


def last_verified_start(ws: Path) -> str:
    try:
        txt = (ws / "logs" / "nexus_control.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = START_RE.findall(txt)
    return found[-1] if found else ""


def ack_stale(ws: Path, errors: list[str]) -> dict:
    """Fehlerzeilen VOR dem letzten verifizierten Start sind veraltet - aber nur, wenn Health jetzt 200 ist."""
    if not health_ok():
        return {"ok": False, "detail": "Health nicht 200 - Fehler sind NICHT veraltet, kein Quittieren"}
    st = last_verified_start(ws)
    if not st:
        return {"ok": False, "detail": "kein verifizierter Start im nexus_control.log"}
    p = _safe(_ack_file(ws)); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"acked_until": st, "reason": "Health 200 + START local verified"}), encoding="utf-8")
    n = sum(1 for e in errors if is_acked(e, ws))
    return {"ok": health_ok(), "detail": f"Health 200, letzter verifizierter Start {st}; {n} veraltete Fehlerzeilen quittiert"}


def rotate_logs(ws: Path) -> dict:
    logs = _safe(ws / "logs")
    arch = _safe(logs / "_archive"); arch.mkdir(exist_ok=True)
    old = [p for p in logs.glob("ollama-*.log") if time.time() - p.stat().st_mtime > 3 * 86400]
    if not old:
        return {"ok": True, "detail": "keine alten Logs"}
    z = _safe(arch / f"ollama_logs_{datetime.now():%Y%m%d_%H%M%S}.zip")
    before = sum(p.stat().st_size for p in old)
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in old:
            zf.write(p, p.name)
    with zipfile.ZipFile(z) as zf:
        bad = zf.testzip(); names = set(zf.namelist())
    if bad or names != {p.name for p in old}:
        return {"ok": False, "detail": f"Zip-Pruefung fehlgeschlagen ({bad}); nichts geloescht"}
    removed = 0
    for p in old:
        try:
            _safe(p).unlink(); removed += 1
        except OSError:
            pass
    left = [p for p in old if p.exists()]
    return {"ok": not left, "detail": f"{removed} Logs ({before // 1048576} MB) -> {z.name} ({z.stat().st_size // 1048576} MB), offen: {len(left)}"}


def _ollama(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def unload_ollama(ws: Path) -> dict:
    try:
        models = [m.get("name") for m in _ollama("/api/ps").get("models", [])]
        for m in models:
            _ollama("/api/generate", {"model": m, "keep_alive": 0})
        left = [m.get("name") for m in _ollama("/api/ps").get("models", [])]
        return {"ok": not left, "detail": f"entladen: {models or 'keine'}; noch geladen: {left or 'keine'}"}
    except Exception as e:
        return {"ok": False, "detail": f"Ollama nicht erreichbar: {e}"}


def restart_nexus(ws: Path) -> dict:
    root = ws.parent
    script = root / "restart_nexus_all.ps1"
    if not script.is_file():
        return {"ok": False, "detail": "restart_nexus_all.ps1 fehlt"}
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", str(script)], cwd=str(root), creationflags=0x08000000 | 0x00004000)
    for _ in range(30):
        time.sleep(3)
        if health_ok():
            return {"ok": True, "detail": "Neustart headless, Health 200"}
    return {"ok": False, "detail": "Neustart ausgeloest, Health nach 90 s nicht 200"}


def run(errors: list[str], ws: Path | None = None, actions: list[str] | None = None) -> list[dict]:
    ws = ws or WORKSPACE
    out = []
    for a in (actions if actions is not None else plan(errors, ws)):
        if a not in ALLOWED:
            res = {"ok": False, "detail": "nicht auf Whitelist - verweigert"}
        else:
            try:
                res = {"ack_stale": lambda: ack_stale(ws, errors), "rotate_logs": lambda: rotate_logs(ws),
                       "unload_ollama": lambda: unload_ollama(ws), "restart_nexus": lambda: restart_nexus(ws)}[a]()
            except Exception as e:
                res = {"ok": False, "detail": f"Fehler: {e}"}
        rec = {"action": a, "result": "PASS" if res["ok"] else "FAIL", "detail": res["detail"]}
        try:
            _audit(ws, rec)
        except Exception:
            pass
        out.append(rec)
    return out
