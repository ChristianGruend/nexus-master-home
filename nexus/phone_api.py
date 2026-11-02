"""NEXUS Phone 002: lokale Telefon-Daten-Routen (Kontakte, Notizen, Kalender, Timer, Geraet).

Alle Speicher sind lokale JSON-Dateien, atomar geschrieben. Loeschen archiviert immer zuerst
nach <name>_deleted.jsonl. Keine externen Dienste. Zugriff wie /api/memory, /api/files.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "workspace" / "phone_data"
CONTACTS_PATH = ROOT / "workspace" / "apex" / "contacts.json"

MAX_TITLE = 200
MAX_TEXT = 10000
MAX_NAME = 120
MAX_FIELD = 64
MAX_ALIASES = 10
MAX_ITEMS = 5000
MAX_TIMER_S = 7 * 24 * 3600

_lock = threading.RLock()
_RE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RE_PHONE = re.compile(r"^\+?[0-9 ()/.-]{3,32}$")


def _paths() -> dict[str, Path]:
    return {
        "notes": DATA_DIR / "notes.json",
        "calendar": DATA_DIR / "calendar.json",
        "timers": DATA_DIR / "timers.json",
    }


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _err(status: int, code: str, **kw: Any) -> tuple[int, dict[str, Any]]:
    return status, {"success": False, "error": code, **kw}


def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    except Exception:
        raise RuntimeError("STORE_CORRUPT:" + path.name)


def _archive(path: Path, item: dict[str, Any]) -> None:
    arch = path.with_name(path.stem + "_deleted.jsonl")
    arch.parent.mkdir(parents=True, exist_ok=True)
    with arch.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"deleted_at": _now_iso(), "item": item}, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _str(v: Any, limit: int) -> str | None:
    if v is None:
        return None
    if not isinstance(v, (str, int, float)):
        return None
    s = str(v).strip()
    return s if len(s) <= limit else None


def _list_store(name: str) -> list[dict[str, Any]]:
    data = _read(_paths()[name], {"items": []})
    items = data.get("items") if isinstance(data, dict) else data
    return [i for i in (items or []) if isinstance(i, dict)]


def _save_store(name: str, items: list[dict[str, Any]]) -> None:
    _atomic_write(_paths()[name], {"version": 1, "items": items})


def _delete_from(name: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    iid = str(body.get("id") or "")
    if not _RE_ID.match(iid):
        return _err(400, "BAD_ID")
    with _lock:
        items = _list_store(name)
        hit = next((i for i in items if i.get("id") == iid), None)
        if not hit:
            return _err(404, "NOT_FOUND")
        _archive(_paths()[name], hit)
        _save_store(name, [i for i in items if i.get("id") != iid])
    return 200, {"success": True, "id": iid}


# ------------------------------------------------------------------ contacts
def _contacts_doc() -> dict[str, Any]:
    data = _read(CONTACTS_PATH, {"contacts": []})
    if isinstance(data, list):
        data = {"contacts": data}
    if not isinstance(data.get("contacts"), list):
        data["contacts"] = []
    return data


def _contact_public(c: dict[str, Any]) -> dict[str, Any]:
    return {k: c.get(k) for k in ("id", "name", "aliases", "whatsapp", "phone", "email", "note") if c.get(k) is not None}


def contacts_get(qs: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    q = ((qs.get("q") or [""])[0] or "").strip().lower()[:MAX_NAME]
    with _lock:
        doc = _contacts_doc()
    out = []
    for c in doc["contacts"]:
        if not isinstance(c, dict) or c.get("example"):
            continue
        if q:
            hay = " ".join([str(c.get("name") or "")] + [str(a) for a in c.get("aliases") or []] +
                           [str(c.get("phone") or ""), str(c.get("whatsapp") or ""), str(c.get("email") or "")]).lower()
            if q not in hay:
                continue
        out.append(_contact_public(c))
    out.sort(key=lambda c: str(c.get("name") or "").lower())
    return 200, {"success": True, "count": len(out), "contacts": out}


def contacts_post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    name = _str(body.get("name"), MAX_NAME)
    if not name:
        return _err(400, "NAME_REQUIRED")
    fields: dict[str, Any] = {"name": name}
    for k in ("phone", "whatsapp"):
        if body.get(k) not in (None, ""):
            v = _str(body.get(k), 32)
            if not v or not _RE_PHONE.match(v):
                return _err(400, "BAD_" + k.upper())
            fields[k] = v
    if body.get("email") not in (None, ""):
        v = _str(body.get("email"), 254)
        if not v or "@" not in v:
            return _err(400, "BAD_EMAIL")
        fields["email"] = v
    if body.get("note") not in (None, ""):
        v = _str(body.get("note"), 1000)
        if v is None:
            return _err(400, "NOTE_TOO_LONG")
        fields["note"] = v
    if body.get("aliases") is not None:
        al = body.get("aliases")
        if not isinstance(al, list) or len(al) > MAX_ALIASES:
            return _err(400, "BAD_ALIASES")
        clean = [_str(a, MAX_NAME) for a in al]
        if any(not a for a in clean):
            return _err(400, "BAD_ALIASES")
        fields["aliases"] = clean
    iid = body.get("id")
    with _lock:
        doc = _contacts_doc()
        cs = doc["contacts"]
        if iid:
            iid = str(iid)
            if not _RE_ID.match(iid):
                return _err(400, "BAD_ID")
            hit = next((c for c in cs if isinstance(c, dict) and c.get("id") == iid), None)
            if not hit:
                return _err(404, "NOT_FOUND")
            hit.update(fields)
            hit["updated"] = _now_iso()
            created = False
        else:
            if len(cs) >= MAX_ITEMS:
                return _err(400, "TOO_MANY")
            hit = {"id": _new_id(), "aliases": [], **fields, "created": _now_iso()}
            cs.append(hit)
            created = True
        _atomic_write(CONTACTS_PATH, doc)
    return 200, {"success": True, "created": created, "contact": _contact_public(hit)}


def contacts_delete(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    iid = str(body.get("id") or "")
    if not _RE_ID.match(iid):
        return _err(400, "BAD_ID")
    with _lock:
        doc = _contacts_doc()
        hit = next((c for c in doc["contacts"] if isinstance(c, dict) and c.get("id") == iid), None)
        if not hit:
            return _err(404, "NOT_FOUND")
        _archive(CONTACTS_PATH, hit)
        doc["contacts"] = [c for c in doc["contacts"] if not (isinstance(c, dict) and c.get("id") == iid)]
        _atomic_write(CONTACTS_PATH, doc)
    return 200, {"success": True, "id": iid}


# ------------------------------------------------------------------ notes
def notes_get(qs: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    q = ((qs.get("q") or [""])[0] or "").strip().lower()[:MAX_TITLE]
    with _lock:
        items = _list_store("notes")
    if q:
        items = [n for n in items if q in (str(n.get("title") or "") + " " + str(n.get("text") or "")).lower()]
    items.sort(key=lambda n: str(n.get("updated") or n.get("created") or ""), reverse=True)
    return 200, {"success": True, "count": len(items), "notes": items}


def notes_post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    text = _str(body.get("text"), MAX_TEXT)
    if not text:
        return _err(400, "TEXT_REQUIRED_OR_TOO_LONG")
    title = _str(body.get("title"), MAX_TITLE) if body.get("title") not in (None, "") else None
    if body.get("title") not in (None, "") and title is None:
        return _err(400, "TITLE_TOO_LONG")
    iid = body.get("id")
    with _lock:
        items = _list_store("notes")
        if iid:
            iid = str(iid)
            if not _RE_ID.match(iid):
                return _err(400, "BAD_ID")
            hit = next((n for n in items if n.get("id") == iid), None)
            if not hit:
                return _err(404, "NOT_FOUND")
            hit.update({"text": text, "title": title or hit.get("title") or text[:40], "updated": _now_iso()})
        else:
            if len(items) >= MAX_ITEMS:
                return _err(400, "TOO_MANY")
            now = _now_iso()
            hit = {"id": _new_id(), "title": title or text.splitlines()[0][:40], "text": text, "created": now, "updated": now}
            items.append(hit)
        _save_store("notes", items)
    return 200, {"success": True, "note": hit}


# ------------------------------------------------------------------ calendar
def _parse_dt(v: Any) -> datetime | None:
    s = _str(v, 40)
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.astimezone()  # lokale Zeit
    return d


def calendar_get(qs: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    f_raw = (qs.get("from") or [None])[0]
    t_raw = (qs.get("to") or [None])[0]
    f = _parse_dt(f_raw) if f_raw else None
    t = _parse_dt(t_raw) if t_raw else None
    if (f_raw and not f) or (t_raw and not t):
        return _err(400, "BAD_RANGE")
    with _lock:
        items = _list_store("calendar")
    out = []
    for e in items:
        s = _parse_dt(e.get("start"))
        if not s:
            continue
        en = _parse_dt(e.get("end")) or s
        if f and en < f:
            continue
        if t and s > t:
            continue
        out.append(e)
    out.sort(key=lambda e: _parse_dt(e.get("start")))
    return 200, {"success": True, "count": len(out), "events": out}


def calendar_post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    title = _str(body.get("title"), MAX_TITLE)
    if not title:
        return _err(400, "TITLE_REQUIRED_OR_TOO_LONG")
    start = _parse_dt(body.get("start"))
    if not start:
        return _err(400, "BAD_START")
    end = None
    if body.get("end") not in (None, ""):
        end = _parse_dt(body.get("end"))
        if not end or end < start:
            return _err(400, "BAD_END")
    note = None
    if body.get("note") not in (None, ""):
        note = _str(body.get("note"), 2000)
        if note is None:
            return _err(400, "NOTE_TOO_LONG")
    ev = {"title": title, "start": start.isoformat(timespec="minutes"),
          "end": end.isoformat(timespec="minutes") if end else None, "note": note}
    iid = body.get("id")
    with _lock:
        items = _list_store("calendar")
        if iid:
            iid = str(iid)
            if not _RE_ID.match(iid):
                return _err(400, "BAD_ID")
            hit = next((e for e in items if e.get("id") == iid), None)
            if not hit:
                return _err(404, "NOT_FOUND")
            hit.update(ev)
            hit["updated"] = _now_iso()
        else:
            if len(items) >= MAX_ITEMS:
                return _err(400, "TOO_MANY")
            hit = {"id": _new_id(), **ev, "created": _now_iso()}
            items.append(hit)
        _save_store("calendar", items)
    return 200, {"success": True, "event": hit}


# ------------------------------------------------------------------ timers
def _timer_view(t: dict[str, Any], now: float) -> dict[str, Any]:
    rem = max(0.0, float(t.get("due_ts", 0)) - now)
    state = t.get("state") or "active"
    if state == "active" and rem <= 0:
        state = "done"
    return {**t, "remaining_s": round(rem, 1) if state == "active" else 0, "done": state == "done", "state": state}


def timer_get(qs: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    show_all = (qs.get("all") or ["0"])[0] in ("1", "true")
    now = time.time()
    with _lock:
        items = [_timer_view(t, now) for t in _list_store("timers")]
    if not show_all:
        cutoff = now - 3600
        items = [t for t in items if t["state"] == "active" or (t["state"] == "done" and float(t.get("due_ts", 0)) >= cutoff)]
    items.sort(key=lambda t: float(t.get("due_ts", 0)))
    return 200, {"success": True, "server_time": _now_iso(), "count": len(items), "timers": items}


def timer_post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        secs = int(body.get("seconds"))
    except (TypeError, ValueError):
        return _err(400, "BAD_SECONDS")
    if secs < 1 or secs > MAX_TIMER_S:
        return _err(400, "BAD_SECONDS", max=MAX_TIMER_S)
    label = _str(body.get("label") or "Timer", MAX_TITLE)
    if not label:
        return _err(400, "LABEL_TOO_LONG")
    now = time.time()
    t = {"id": _new_id(), "label": label, "seconds": secs, "created_ts": now, "due_ts": now + secs,
         "due": datetime.fromtimestamp(now + secs).astimezone().isoformat(timespec="seconds"), "state": "active"}
    with _lock:
        items = _list_store("timers")
        # alte erledigte/abgebrochene (>24h) aufraeumen -> vorher archivieren
        keep = []
        for x in items:
            if (x.get("state") in ("cancelled", "done") or float(x.get("due_ts", 0)) < now) and float(x.get("due_ts", 0)) < now - 86400:
                _archive(_paths()["timers"], x)
            else:
                keep.append(x)
        if len(keep) >= 200:
            return _err(400, "TOO_MANY")
        keep.append(t)
        _save_store("timers", keep)
    return 200, {"success": True, "timer": _timer_view(t, now)}


def timer_cancel(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    iid = str(body.get("id") or "")
    if not _RE_ID.match(iid):
        return _err(400, "BAD_ID")
    with _lock:
        items = _list_store("timers")
        hit = next((t for t in items if t.get("id") == iid), None)
        if not hit:
            return _err(404, "NOT_FOUND")
        if hit.get("state") == "cancelled":
            return _err(409, "ALREADY_CANCELLED")
        hit["state"] = "cancelled"
        hit["cancelled"] = _now_iso()
        _save_store("timers", items)
    return 200, {"success": True, "id": iid, "timer": _timer_view(hit, time.time())}


# ------------------------------------------------------------------ device
DEVICE_ACTIONS = ("open_app", "screen_state", "list_apps")


def _pa():
    from nexus import phone_actions
    return phone_actions


def device_get(qs: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    pa = _pa()
    connect = (qs.get("connect") or ["0"])[0] in ("1", "true")
    dev = pa.select_device(connect=connect)
    out: dict[str, Any] = {"success": True, "connected": dev.get("status") == "OK",
                           "status": dev.get("status"), "reason": dev.get("reason"),
                           "transport": dev.get("transport"), "battery": None,
                           "actions": list(DEVICE_ACTIONS)}
    try:
        out.update(pa.watchdog_state())
        if dev.get("status") == "OK":
            out["last_seen"] = pa.watchdog_state().get("last_seen")
        out["identity_by"] = dev.get("identity_by")
        try:
            out.update(pa.transport_path(dev.get("serial") or "") if dev.get("serial") else {"transport_path": None})
        except Exception:
            out["transport_path"] = "unknown"
    except Exception:
        pass
    if dev.get("status") == "OK":
        r = pa._adb(dev["serial"], ["shell", "dumpsys", "battery"], 8)
        if r.get("ok"):
            bat: dict[str, Any] = {}
            for key in ("level", "status", "temperature", "AC powered", "USB powered"):
                m = re.search(r"^\s*" + re.escape(key) + r":\s*(\S+)", r.get("out") or "", re.M)
                if m:
                    bat[key.replace(" ", "_").lower()] = m.group(1)
            out["battery"] = bat or None
    return 200, out


def device_post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    action = _str(body.get("action"), 32)
    if action not in DEVICE_ACTIONS:
        return _err(400, "ACTION_NOT_ALLOWED", allowed=list(DEVICE_ACTIONS))
    pa = _pa()
    if action == "list_apps":
        return 200, {"success": True, "apps": pa.phone_app_names()}
    if action == "open_app":
        app = _str(body.get("app"), 64)
        if not app or not pa.find_phone_app(app):
            return _err(400, "UNKNOWN_APP", apps=pa.phone_app_names())
        r = pa.open_phone_app(app, source="api_device")
        return (200 if r.get("success") else 503), r
    dev = pa.select_device(connect=False)
    if dev.get("status") != "OK":
        return 503, {"success": False, "error": "DEVICE_UNAVAILABLE", "reason": dev.get("reason")}
    return 200, {"success": True, **pa.screen_state(dev["serial"])}


# ------------------------------------------------------------------ dispatch
GET_ROUTES = {
    "/api/contacts": contacts_get,
    "/api/notes": notes_get,
    "/api/calendar": calendar_get,
    "/api/timer": timer_get,
    "/api/device": device_get,
}
POST_ROUTES = {
    "/api/contacts": contacts_post,
    "/api/contacts/delete": contacts_delete,
    "/api/notes": notes_post,
    "/api/notes/delete": lambda b: _delete_from("notes", b),
    "/api/calendar": calendar_post,
    "/api/calendar/delete": lambda b: _delete_from("calendar", b),
    "/api/timer": timer_post,
    "/api/timer/cancel": timer_cancel,
    "/api/device": device_post,
}


def handle_get(path: str, qs: dict[str, list[str]]) -> tuple[int, dict[str, Any]] | None:
    fn = GET_ROUTES.get(path)
    if not fn:
        return None
    try:
        return fn(qs or {})
    except RuntimeError as e:
        return _err(500, str(e)[:80])


def handle_post(path: str, body: Any) -> tuple[int, dict[str, Any]] | None:
    fn = POST_ROUTES.get(path)
    if not fn:
        return None
    if not isinstance(body, dict):
        return _err(400, "BODY_MUST_BE_OBJECT")
    try:
        return fn(body)
    except RuntimeError as e:
        return _err(500, str(e)[:80])
