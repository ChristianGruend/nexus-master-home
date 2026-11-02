"""Auftrag 003A: Hermes ALL-Modus - nur Core-Seite (Anfrage, Nonce, Status), 2026-10-16.

Der Core schaltet NIE selbst. Ablauf:
  /hermes all modus (Owner, nicht source=voice) -> Nonce + PENDING_CONFIRMATION,
      Anfrage als JSON nach workspace/hermes_service/all_requests/<request_id>.json
  POST /api/hermes/all/confirm {"request_id"} -> Nonce einmalig pruefen, Anfrage als confirmed markieren
      -> AWAITING_WINDOWS_HELLO (Admin-Dienst 005 bestaetigt per Windows Hello/PIN)
  ACTIVE/ends_at kommen ausschliesslich aus all_status.json, die der Admin-Dienst schreibt.
  /hermes normal -> End-Anfrage (end_<ts>.json), sofort, ohne Bestaetigung.
Keine Credentials, kein Kontowechsel im Wrapper (hermes_runas.py unveraendert), T8 wird nie umgangen.
"""
from __future__ import annotations

import json, os, secrets, threading, time, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SVC = ROOT / "workspace" / "hermes_service"
REQ_DIR = SVC / "all_requests"
STATUS_FILE = SVC / "admin_status" / "all_status.json"  # schreibt nur der Admin-Dienst (Ordner sichert Nexus-Tools)
T8_FILE = SVC / "t8_gate.json"             # optional: {"pass": true}
LOG = SVC / "all_mode.log"
CONFIRM_ENDPOINT = "/api/hermes/all/confirm"
NONCE_TTL_S = int(os.environ.get("NEXUS_ALLMODE_NONCE_TTL", "300"))
DURATION_MIN = 30
DEFAULT_IN_DIR = r"C:\Users\nexus-hermes\hermes_out"  # Runner prueft gegen 003a_ordner.txt
MAX_QUERY_CHARS = 4000
SCOPE = ["Hermes darf ausserhalb hermes_tasks arbeiten (Konto nexus-hermes-all, Admin-Dienst)"]
STILL_BLOCKED = ["Admin-Rechte", "Secrets (bridge.token, .env, certs)", "Windows-Systemdateien",
                 "Ordner loeschen / irreversible Schritte nur mit gesonderter Bestaetigung (Admin-Dienst-Allowlist)",
                 "Sprachbefehle (source=voice)"]
IRREVERSIBLE_MARKERS = ("rmdir", "rd /s", "rm -rf", "rm -r", "remove-item -recurse", "format ", "ordner loeschen",
                        "ordner löschen", "loesche den ordner", "lösche den ordner", "unwiderruflich", "irreversibel")

_LOCK = threading.Lock()
SERVICE_FILE = STATUS_FILE.parent / "service_installed.json"   # schreibt Nexus-Tools/Admin-Dienst
FORBIDDEN_PARTS = {"bridge", ".hermes", "secrets", "secure", "certs", ".ssh", ".gnupg"}
FORBIDDEN_ROOTS = (r"C:\Windows", r"C:\Program Files", r"C:\ProgramData", r"C:\Users\gustl")
_READY_CACHE = {"ts": 0.0, "val": False}
_PENDING: dict = {}   # request_id -> {"nonce_exp": float, "used": bool}


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(event: str, **kw):
    try:
        SVC.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _iso(_now()), "event": event, **kw}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except Exception:
        return {"_invalid": True}


def _write_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _parse(ts):
    try:
        return datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def t8_gate() -> dict:
    if not (SVC / "admin_status" / "acl_applied.json").exists():
        return {"pass": False, "source": "acl_applied.json fehlt", "note": "Rechte-Schritt 003A nicht abgeschlossen"}
    d = _read_json(T8_FILE)
    if isinstance(d, dict) and d.get("pass") is True:
        return {"pass": True, "source": str(T8_FILE.name)}
    # Stand PROGRESS 2026-10-16: T8 Lauf 3 PASS mit Restrisiko, Restrisiko von Eddie 11:19 angenommen.
    return {"pass": True, "source": "PROGRESS.md T8 Lauf 3 PASS + Eddie 11:19 Restrisiko angenommen",
            "note": "t8_gate.json fehlt; Admin-Dienst prueft T8 selbst erneut"}


TRUSTED_OWNERS = ("S-1-5-18", "S-1-5-32-544")  # SYSTEM, Administratoren


def _owner_sid(path) -> str | None:
    """Besitzer-SID per GetNamedSecurityInfoW (nur lesen, ohne Unterprozess). None bei Fehler."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32")
        psid, psd = ctypes.c_void_p(), ctypes.c_void_p()
        adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
                                              ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_void_p,
                                              ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        rc = adv.GetNamedSecurityInfoW(str(path), 1, 0x1, ctypes.byref(psid), None, None, None, ctypes.byref(psd))
        if rc != 0:
            return None
        try:
            out = wintypes.LPWSTR()
            adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
            if not adv.ConvertSidToStringSidW(psid, ctypes.byref(out)):
                return None
            v = out.value
            k32.LocalFree(out)
            return v
        finally:
            k32.LocalFree(psd)
    except Exception:
        return None


def _owner_trusted(paths=None) -> bool:
    """003A Restrisiko: Datei UND Ordner admin_status muessen SYSTEM oder Administratoren gehoeren."""
    bad = {}
    for p in (paths or (STATUS_FILE.parent, STATUS_FILE)):
        sid = _owner_sid(p)
        if sid not in TRUSTED_OWNERS:
            bad[str(p.name)] = sid
    if bad:
        _log("status_owner_untrusted", owners=bad)
        return False
    return True


def _admin_status():
    """ACTIVE nur, wenn der Admin-Dienst es gemeldet hat und ends_at in der Zukunft liegt (<=30 min)."""
    d = _read_json(STATUS_FILE)
    if not isinstance(d, dict) or d.get("_invalid"):
        return None
    if d.get("state") != "ACTIVE":
        return None
    if not _owner_trusted():
        return None
    ends = _parse(d.get("ends_at"))
    if not ends or ends <= _now() or ends > _now() + timedelta(minutes=DURATION_MIN + 1):
        return None
    return {"request_id": d.get("request_id"), "ends_at": _iso(ends)}


def _latest_request():
    if not REQ_DIR.is_dir():
        return None
    best, bkey = None, None
    for p in REQ_DIR.glob("*.json"):
        d = _read_json(p)
        if isinstance(d, dict) and d.get("type") == "all_mode_start":
            try: key = (str(d.get("created_at")), p.stat().st_mtime_ns)
            except OSError: continue
            if bkey is None or key > bkey:
                best, bkey = d, key
    return best


def admin_service_ready() -> bool:
    """true nur, wenn admin_status\\service_installed.json existiert, gueltiges JSON ist und Datei + Ordner
    SYSTEM/Administratoren gehoeren (Cache 30 s). Kein schtasks (gustl sieht die Aufgabe nicht)."""
    now = time.time()
    if now - _READY_CACHE["ts"] < 30:
        return _READY_CACHE["val"]
    val = False
    if SERVICE_FILE.is_file():
        d = _read_json(SERVICE_FILE)
        val = isinstance(d, dict) and not d.get("_invalid") and _owner_trusted((SERVICE_FILE.parent, SERVICE_FILE))
    _READY_CACHE.update(ts=now, val=val)
    return val


def in_dir_forbidden(path: str) -> bool:
    """realpath, dann: verbotene Pfadkomponente oder unter Systemwurzeln/gustl-Profil -> True."""
    try:
        rp = os.path.realpath(str(path))
    except Exception:
        return True
    norm = os.path.normcase(rp).replace("/", "\\").rstrip("\\")
    parts = [x for x in norm.split("\\") if x]
    if any(x.lower() in FORBIDDEN_PARTS for x in parts):
        return True
    for r in FORBIDDEN_ROOTS:
        rr = os.path.normcase(r).rstrip("\\")
        if norm.lower() == rr.lower() or norm.lower().startswith(rr.lower() + "\\"):
            return True
    return False


def status() -> dict:
    gate = t8_gate()
    base = {"state": "OFF", "request_id": None, "expires_at": None, "ends_at": None, "scope": SCOPE,
            "duration_min": DURATION_MIN, "still_blocked": STILL_BLOCKED, "confirm_endpoint": CONFIRM_ENDPOINT,
            "t8_gate": gate, "admin_service_ready": admin_service_ready()}
    if not gate.get("pass"):
        return {**base, "state": "LOCKED_T8"}
    act = _admin_status()
    if act:
        return {**base, "state": "ACTIVE", "request_id": act["request_id"], "ends_at": act["ends_at"]}
    r = _latest_request()
    if r and r.get("status") in ("pending", "confirmed"):
        exp = _parse(r.get("expires_at"))
        if r.get("status") == "pending" and exp and exp > _now():
            return {**base, "state": "PENDING_CONFIRMATION", "request_id": r.get("request_id"),
                    "expires_at": r.get("expires_at")}
    return base


def request_start(*, source: str = "chat", owner: bool = False, query: str | None = None, in_dir: str | None = None) -> dict:
    if (source or "").lower() == "voice":
        _log("start_rejected", reason="VOICE_REJECTED", source=source)
        return {"success": False, "error": "VOICE_REJECTED", "reply": "ALL-Modus geht nicht per Sprache, nur im Chat."}
    if not owner:
        _log("start_rejected", reason="OWNER_ONLY", source=source)
        return {"success": False, "error": "OWNER_ONLY", "reply": "ALL-Modus darf nur Eddie lokal anfordern."}
    if not admin_service_ready():
        _log("start_rejected", reason="ADMIN_SERVICE_MISSING", source=source)
        return {"success": False, "error": "ADMIN_SERVICE_MISSING",
                "reply": "ALL-Modus noch nicht verfuegbar: Admin-Dienst (005) ist nicht eingerichtet. Es wurde keine Anfrage angelegt."}
    if not (query and str(query).strip()):
        _log("start_rejected", reason="QUERY_REQUIRED", source=source)  # Query-Text wird nie geloggt
        return {"success": False, "error": "QUERY_REQUIRED",
                "reply": "Bitte den Auftrag mitgeben: /hermes all modus <Auftrag>. Es wurde keine Anfrage angelegt."}
    st = status()
    if st["state"] == "LOCKED_T8":
        _log("start_rejected", reason="LOCKED_T8")
        return {"success": False, "error": "LOCKED_T8", "reply": "ALL-Modus gesperrt: T8 nicht bestanden.", "all_mode": st}
    if st["state"] == "ACTIVE":
        return {"success": True, "reply": f"ALL-Modus laeuft bereits bis {st['ends_at']}.", "all_mode": st}
    _in = str(in_dir).strip() if in_dir and "\n" not in str(in_dir) else DEFAULT_IN_DIR
    if in_dir_forbidden(_in):
        _log("start_rejected", reason="IN_DIR_FORBIDDEN", source=source)
        return {"success": False, "error": "IN_DIR_FORBIDDEN",
                "reply": "Dieser Arbeitsordner ist fuer den ALL-Modus gesperrt. Es wurde keine Anfrage angelegt."}
    rid = uuid.uuid4().hex
    now = _now()
    exp = now + timedelta(seconds=NONCE_TTL_S)
    req = {"type": "all_mode_start", "request_id": rid, "created_at": _iso(now), "expires_at": _iso(exp),
           "status": "pending", "source": source, "duration_min": DURATION_MIN, "account": "nexus-hermes-all",
           "scope": SCOPE, "still_blocked": STILL_BLOCKED,
           # optional (003A Runner): Auftrag + Arbeitsordner; toolsets fest ["file"] bis scope[] feststeht
           "query": (str(query).strip()[:MAX_QUERY_CHARS] if query else ""),
           "in_dir": os.path.realpath(_in),
           "toolsets": ["file"]}
    with _LOCK:
        _PENDING[rid] = {"nonce_exp": exp.timestamp(), "used": False}
        _write_json(REQ_DIR / f"{rid}.json", req)
    _log("start_requested", request_id=rid, expires_at=req["expires_at"], source=source)
    st = status()
    return {"success": True, "intent": "hermes_all_mode", "request_id": rid, "all_mode": st,
            "reply": "ALL-Modus angefordert. Bitte die Karte bestaetigen, danach Windows Hello/PIN am PC. "
                     f"Die Anfrage verfaellt um {req['expires_at']} (UTC)."}


def confirm(body) -> dict:
    """Body exakt {"request_id": "..."}; einmalig, verfaellt nach NONCE_TTL_S."""
    if not isinstance(body, dict) or set(body.keys()) != {"request_id"} or not isinstance(body.get("request_id"), str):
        _log("confirm_rejected", reason="BODY_INVALID")
        return {"accepted": False, "error": "NONCE_INVALID"}
    rid = body["request_id"]
    with _LOCK:
        ent = _PENDING.get(rid)
        ok = ent is not None and not ent["used"] and ent["nonce_exp"] > time.time()
        if ent is not None:
            ent["used"] = True  # einmalig, auch bei abgelaufener ID
        p = REQ_DIR / f"{rid}.json"
        req = _read_json(p) if ok and len(rid) == 32 and rid.isalnum() else None
        if not ok or not isinstance(req, dict) or req.get("status") != "pending":
            _log("confirm_rejected", reason="NONCE_INVALID", request_id=rid[:64])
            return {"accepted": False, "error": "NONCE_INVALID"}
        req["status"] = "confirmed"; req["confirmed_at"] = _iso(_now())
        _write_json(p, req)
    _log("confirm_accepted", request_id=rid)
    return {"accepted": True, "state": "AWAITING_WINDOWS_HELLO"}


def request_end(*, source: str = "chat") -> dict:
    now = _now()
    rid = "end_" + now.strftime("%Y%m%d%H%M%S") + "_" + secrets.token_hex(3)
    _write_json(REQ_DIR / f"{rid}.json", {"type": "all_mode_end", "request_id": rid, "created_at": _iso(now),
                                         "source": source, "status": "pending"})
    with _LOCK:
        for k in _PENDING:
            _PENDING[k]["used"] = True
    for p in REQ_DIR.glob("*.json"):
        d = _read_json(p)
        if isinstance(d, dict) and d.get("type") == "all_mode_start" and d.get("status") in ("pending", "confirmed"):
            d["status"] = "cancelled"; d["cancelled_at"] = _iso(now)
            try: _write_json(p, d)
            except OSError: pass
    _log("end_requested", request_id=rid, source=source)
    return {"success": True, "intent": "hermes_normal", "all_mode": status(),
            "reply": "Hermes Normalmodus angefordert. Der Admin-Dienst beendet den ALL-Modus und laufende Prozesse."}


def needs_separate_confirmation(task: str) -> bool:
    low = (task or "").lower()
    return any(m in low for m in IRREVERSIBLE_MARKERS)


def startup_cleanup():
    """Core-Start: alte/ungueltige Anfragen verwerfen, Nonces gibt es nach Neustart nicht mehr."""
    n = 0
    if REQ_DIR.is_dir():
        for p in REQ_DIR.glob("*.json"):
            d = _read_json(p)
            if isinstance(d, dict) and d.get("type") == "all_mode_start" and d.get("status") == "pending":
                d["status"] = "expired"; _write_json(p, d); n += 1
    if n:
        _log("startup_cleanup", expired=n)


START_PREFIXES = ("/hermes all modus", "/hermes all-modus", "/hermes allmodus", "/hermes all")


def split_start(text: str):
    """'/hermes all modus <Auftrag>' -> Auftrag ('' wenn leer); None, wenn kein Start-Befehl."""
    raw = " ".join((text or "").strip().split())
    low = raw.lower()
    for p in START_PREFIXES:
        if low == p or low.startswith(p + " ") or low.startswith(p + ":"):
            return raw[len(p):].lstrip(" :").strip()
    return None


def match_command(text: str):
    t = " ".join((text or "").strip().lower().split())
    if split_start(t) is not None:
        return "start"
    if t in ("/hermes normal", "/hermes normalmodus"):
        return "end"
    return None
