"""Authenticated, durable PC voice commands carried by the existing 005D link.

Transport receipt is never execution success. Mutations require a separate spoken
Ja tied to one stored action. A crash during execution is never retried blindly.
All persistent state lives below the project workspace; no shell is exposed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import queue
import re
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

# Optional installed dependencies may live on C:. Never create their bytecode caches.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "workspace" / "pc_remote"
TOKEN_PATH = ROOT / "workspace" / "secrets" / "pc_remote_token.txt"
CONFIRM_TTL = 60
COMMAND_TTL = 120
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_YES = re.compile(r"ja[.!?]?", re.I)
_NO = re.compile(r"(?:nein|abbrechen|abbruch|stopp)[.!?]?", re.I)
_SERVICE = None
_SERVICE_LOCK = threading.Lock()
_PUBLIC_STATES = {"needs_confirm", "needs_clarification", "done", "failed", "rejected"}


def _prompt(intent: str, args: dict) -> str:
    if intent == "app.open":
        action = f"{args['app']} im kontrollierten PC-Browser oeffnen"
    elif intent == "url.open":
        action = f"die freigegebene Musikseite {args['url']} am PC oeffnen"
    elif intent in ("youtube.search", "media.play_search"):
        action = f"auf YouTube am PC nach {args['query']} suchen"
        if intent == "media.play_search":
            action += " und den ersten gefundenen Titel abspielen"
    else:
        action = {"media.pause": "YouTube am PC pausieren", "media.play": "YouTube am PC abspielen",
                  "media.resume": "YouTube am PC fortsetzen", "media.next": "den naechsten YouTube-Titel am PC abspielen",
                  "media.prev": "den vorherigen YouTube-Titel am PC abspielen", "media.stop": "YouTube am PC stoppen",
                  "volume.up": "den YouTube-Player am PC lauter stellen", "volume.down": "den YouTube-Player am PC leiser stellen",
                  "volume.mute": "den YouTube-Player am PC stummschalten",
                  "volume.set": f"den YouTube-Player am PC auf {args.get('percent')} Prozent Lautstaerke stellen"}[intent]
    return "Soll ich " + action + "? Sage Ja oder Nein."


def _token() -> str:
    value = (os.environ.get("NEXUS_PC_REMOTE_TOKEN") or "").strip()
    if not value:
        try:
            value = TOKEN_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return value if len(value) >= 32 else ""


def authenticate(headers: Any) -> tuple[str | None, str | None]:
    expected = _token()
    if not expected:
        return None, "PC_AUTH_UNCONFIGURED"
    supplied = str(headers.get("Authorization") or headers.get("authorization") or "")
    if not supplied.lower().startswith("bearer ") or not hmac.compare_digest(
            supplied[7:].strip().encode("utf-8"), expected.encode("utf-8")):
        return None, "PC_AUTH_REQUIRED"
    return hashlib.sha256(expected.encode("utf-8")).hexdigest(), None


def capabilities() -> dict[str, Any]:
    return {"protocol": 1, "kind": "pc_command", "auth": "scoped_bearer",
            "auth_configured": bool(_token()), "confirmation": "spoken_ja_per_mutation",
            "confirmation_ttl_s": CONFIRM_TTL, "command_ttl_s": COMMAND_TTL,
            "volume_target": "youtube_player", "acceptance": "NOT_VERIFIED"}


def _write(path: Path, value: dict) -> None:
    if os.name == "nt" and path.resolve().drive.upper() != "G:":
        raise ValueError("PC_STATE_MUST_BE_ON_G")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pc-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


class PCRemoteService:
    def __init__(self, directory: Path, engine=None, executor=None, publisher=None,
                 start_worker: bool = True):
        self.directory = Path(directory)
        self.engine = engine
        self.executor = executor
        self.publisher = publisher
        self.lock = threading.RLock()
        self.pending = queue.Queue()
        self.records: dict[str, dict] = {}
        self.start_worker = start_worker
        self.worker = None
        self.last_error = None
        # Corrupt state blocks service startup instead of forgetting executed IDs.
        for path in sorted((self.directory / "commands").glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or not _ID.fullmatch(str(record.get("id") or "")):
                raise ValueError("PC_COMMAND_STORE_CORRUPT")
            self.records[record["id"]] = record
        for record in list(self.records.values()):
            if record["status"] == "executing":
                self._finish(record, "failed", "Die Ausfuehrung wurde unterbrochen. Der PC-Zustand muss geprueft werden.",
                             error="EXECUTION_UNCERTAIN")
            elif record["status"] in ("queued", "processing"):
                self.pending.put(record["id"])
            elif (not record.get("action_ref_id") and record["status"] in ("needs_confirm", "needs_clarification")
                  and record.get("expires", 0) <= time.time()):
                self._finish(record, "rejected", "Die Rueckfrage ist abgelaufen. Es wurde nichts ausgefuehrt.",
                             error="CONFIRM_EXPIRED")
            self._publish(record)

    def _save(self, record: dict) -> None:
        with self.lock:
            _write(self.directory / "commands" / (record["id"] + ".json"), record)
            self.records[record["id"]] = record

    def _publish(self, record: dict) -> None:
        # Confirmation/clarification receipts are aliases, not another PC action.
        # Publish the original action only, so the phone never asks/speaks twice.
        if record.get("action_ref_id"):
            return
        if (self.publisher and record["status"] in _PUBLIC_STATES
                and record.get("delivered_revision", 0) < record.get("revision", 1)):
            try:
                self.publisher(self.event(record), record["owner"])
                record["delivered_revision"] = record["revision"]
                self._save(record)
            except Exception:
                # The durable record is the receipt; a later duplicate/restart republishes it.
                return

    def event(self, record: dict) -> dict:
        event = {"kind": "event", "ref_id": record["id"], "status": record["status"],
                 "success": record.get("success", False), "reply": record.get("reply", ""),
                 "text": record.get("reply", ""),
                 "event_id": record["id"] + ":" + str(record.get("revision", 1))}
        for key in ("error", "evidence", "confirm_id", "intent", "args", "action_ref_id"):
            if key in record:
                event[key] = record[key]
        if "confirm_id" in record:
            event["expires_in"] = max(0, int(record.get("expires", 0) - time.time()))
        return event

    def _finish(self, record: dict, status: str, reply: str, **extra) -> None:
        with self.lock:
            record.update(status=status, success=status == "done", reply=reply, updated_ts=time.time(),
                          revision=record.get("revision", 0) + 1)
            for key in ("confirm_id", "expires", "error", "evidence"):
                record.pop(key, None)
            record.update(extra)
            self._save(record)
            self._publish(record)

    def _ask(self, record: dict, status: str, prompt: str, **extra) -> None:
        self._finish(record, status, prompt, confirm_id="pc-" + uuid.uuid4().hex,
                     expires=time.time() + CONFIRM_TTL, **extra)

    def accept(self, item: dict, owner: str) -> tuple[bool, dict]:
        iid = item.get("id")
        if not isinstance(iid, str) or not _ID.fullmatch(iid):
            return False, {"error": "PC_ID_INVALID"}
        if not isinstance(item.get("text", ""), str) or len(item.get("text", "")) > 4000:
            return False, {"error": "PC_TEXT_INVALID"}
        if (not isinstance(item.get("utterance", ""), str) or len(item.get("utterance", "")) > 4000
                or not isinstance(item.get("args", {}), dict)):
            return False, {"error": "PC_ENVELOPE_INVALID"}
        try:
            encoded = json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return False, {"error": "PC_ENVELOPE_INVALID"}
        if len(encoded) > 16000:
            return False, {"error": "PC_ENVELOPE_TOO_LARGE"}
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.lock:
            previous = self.records.get(iid)
            if previous:
                if previous["owner"] != owner or previous["fingerprint"] != fingerprint:
                    return False, {"error": "PC_ID_CONFLICT"}
                self._publish(previous)
                return True, {"id": iid, "status": previous["status"], "duplicate": True}
            record = {"id": iid, "owner": owner, "fingerprint": fingerprint,
                      "request": item, "status": "queued", "received_ts": time.time()}
            self._save(record)  # Persist before acknowledging or scheduling anything.
            self.pending.put(iid)
            self._ensure_worker()
            return True, {"id": iid, "status": "queued", "duplicate": False}

    def _ensure_worker(self) -> None:
        if self.start_worker and (self.worker is None or not self.worker.is_alive()):
            self.worker = threading.Thread(target=self._run, name="nexus-pc-remote", daemon=True)
            self.worker.start()

    def _run(self) -> None:
        while True:
            try:
                iid = self.pending.get(timeout=2)
            except queue.Empty:
                try:
                    with self.lock:
                        for record in list(self.records.values()):
                            if (not record.get("action_ref_id") and record["status"] in ("needs_confirm", "needs_clarification")
                                    and record.get("expires", 0) <= time.time()):
                                self._finish(record, "rejected", "Die Rueckfrage ist abgelaufen. Es wurde nichts ausgefuehrt.",
                                             error="CONFIRM_EXPIRED")
                            self._publish(record)
                except Exception as exc:
                    self.last_error = "PC_STORE_" + type(exc).__name__
                continue
            try:
                self._process(iid)
            except Exception as exc:
                # A disk failure must not kill processing of unrelated queued requests.
                # Never requeue an action which may already have touched the machine.
                self.last_error = "PC_STORE_" + type(exc).__name__
            finally:
                self.pending.task_done()

    def process_pending(self) -> None:
        """Synchronous drain for tests; production has one serialized worker."""
        while not self.pending.empty():
            iid = self.pending.get_nowait()
            try:
                self._process(iid)
            finally:
                self.pending.task_done()

    def _process(self, iid: str) -> None:
        with self.lock:
            record = self.records[iid]
            if record["status"] not in ("queued", "processing"):
                return
            record["status"] = "processing"
            self._save(record)
        try:
            item = record["request"]
            ts = item.get("ts")
            if (isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts)
                    or not -60 <= time.time() - ts <= COMMAND_TTL):
                return self._finish(record, "rejected", "Der PC-Befehl ist veraltet oder hat keinen gueltigen Zeitstempel.",
                                    error="PC_COMMAND_EXPIRED")
            intent = item.get("intent")
            if intent in ("command.confirm", "command.clarify"):
                return self._answer(record)
            if intent is not None and not isinstance(intent, str):
                return self._finish(record, "rejected", "Ungueltiger PC-Befehl.", error="PC_INTENT_INVALID")
            if not intent:
                from nexus.voice.remote_intents import interpret
                parsed = interpret(item.get("utterance") or item.get("text") or "", self.engine)
                if parsed.get("question"):
                    if parsed.get("error") in ("PC_COMMAND_BLOCKED", "PC_COMMAND_NEGATED", "PC_MULTIPLE_COMMANDS"):
                        return self._finish(record, "rejected", parsed["question"], error=parsed["error"])
                    return self._ask(record, "needs_clarification", parsed["question"],
                                     slot=parsed.get("slot"), error=parsed.get("error", "PC_COMMAND_UNCLEAR"))
                intent, args = parsed.get("intent"), parsed.get("args", {})
            else:
                args = item.get("args", {})
            self._prepare(record, intent, args)
        except Exception as exc:
            self._finish(record, "failed", "Der PC-Befehl konnte nicht sicher verarbeitet werden.",
                         error="PC_PROCESSING_" + type(exc).__name__)

    def _prepare(self, record: dict, intent: str, args: dict) -> None:
        from nexus.computer.remote_executor import validate
        try:
            normalized = validate(intent, args)
        except (ValueError, TypeError) as exc:
            return self._finish(record, "rejected", "Dieser PC-Befehl oder seine Argumente sind nicht freigegeben.",
                                error=str(exc)[:100] or "PC_ACTION_NOT_ALLOWED")
        record.update(intent=intent, args=normalized)
        if intent == "pc.status":
            return self._execute(record)
        if record["request"].get("source") != "voice":
            return self._finish(record, "rejected", "PC-Aktionen brauchen einen Sprachbefehl und ein gesprochenes Ja.",
                                error="PC_VOICE_REQUIRED")
        self._ask(record, "needs_confirm", _prompt(intent, normalized))

    def _answer(self, record: dict) -> None:
        item, args = record["request"], record["request"].get("args", {})
        if set(args) != {"confirm_id", "answer"} or item.get("source") != "voice":
            return self._finish(record, "rejected", "Eine gesprochene Antwort auf die konkrete Rueckfrage ist erforderlich.",
                                error="PC_CONFIRM_INVALID")
        answer, pid = args.get("answer"), args.get("confirm_id")
        if not isinstance(answer, str) or len(answer) > 1000 or not isinstance(pid, str):
            return self._finish(record, "rejected", "Ungueltige Antwort.", error="PC_CONFIRM_INVALID")
        # Do not allow a client-side intent/argument to turn a spoken Nein into Ja.
        if any(item.get(key) and item[key].strip().casefold().rstrip(".!?") != answer.strip().casefold().rstrip(".!?")
               for key in ("text", "utterance")):
            return self._finish(record, "rejected", "Erkannte Sprache und Bestaetigung widersprechen sich.",
                                error="PC_CONFIRM_SPEECH_MISMATCH")
        with self.lock:
            original = next((r for r in self.records.values() if r.get("confirm_id") == pid
                             and r["owner"] == record["owner"] and not r.get("action_ref_id")), None)
        # Exactly one worker owns transitions. Never hold the transport lock over
        # model/browser work: the phone's six-second sync remains an async receipt.
        required = "needs_confirm" if item["intent"] == "command.confirm" else "needs_clarification"
        if not original or original["status"] != required:
            return self._finish(record, "rejected", "Diese Rueckfrage ist unbekannt oder bereits beantwortet.",
                                error="PC_CONFIRM_UNKNOWN")
        if original.get("expires", 0) <= time.time():
            self._finish(original, "rejected", "Die Rueckfrage ist abgelaufen. Es wurde nichts ausgefuehrt.",
                         error="CONFIRM_EXPIRED")
            return self._finish(record, "rejected", "Die Rueckfrage ist abgelaufen.", error="CONFIRM_EXPIRED")
        if _NO.fullmatch(answer.strip()):
            self._finish(original, "rejected", "Abgebrochen. Es wurde nichts ausgefuehrt.", error="PC_CANCELLED")
            return self._finish(record, "rejected", "Abgebrochen. Es wurde nichts ausgefuehrt.", error="PC_CANCELLED")
        if required == "needs_confirm":
            if not _YES.fullmatch(answer.strip()):
                return self._finish(record, "rejected", "Bitte sage ausschliesslich Ja oder Nein.",
                                    error="PC_EXPLICIT_JA_REQUIRED")
            # Consume the confirmation durably before touching the machine.
            with self.lock:
                original["confirmed_by"] = record["id"]
            self._execute(original)
        else:
            original.pop("confirm_id", None)
            slot = original.pop("slot", None)
            if slot == "app":
                from nexus.voice.remote_intents import interpret
                parsed = interpret("oeffne " + answer + " auf dem PC", self.engine)
            elif slot == "query":
                parsed = {"intent": "media.play_search", "args": {"query": answer.strip(), "target": "youtube"}}
            else:
                from nexus.voice.remote_intents import interpret
                parsed = interpret(answer, self.engine)
            if parsed.get("intent"):
                self._prepare(original, parsed["intent"], parsed.get("args", {}))
            else:
                self._ask(original, "needs_clarification", parsed.get("question") or "Bitte wiederhole den vollstaendigen PC-Befehl.",
                          slot=parsed.get("slot"))
        extra = {"action_ref_id": original["id"]}
        for key in ("confirm_id", "expires"):
            if key in original:
                extra[key] = original[key]
        if original.get("evidence"):
            extra["evidence"] = original["evidence"]
        if original.get("error"):
            extra["error"] = original["error"]
        # Replies always refer back to the original action as well.
        self._finish(record, original["status"], original["reply"], **extra)

    def _execute(self, record: dict) -> None:
        with self.lock:
            record.pop("confirm_id", None)
            record.pop("expires", None)
            record.update(status="executing", started_ts=time.time())
            self._save(record)
        try:
            if self.executor:
                result = self.executor(record["intent"], record["args"])
            else:
                from nexus.safety import dispatch as _disp, gate as _gate  # Phase 2 Schritt 7
                if _disp.enabled():
                    if not _gate.check("NEXUS", "pc_music").allowed:
                        result = {"success": False, "error": "PC_GATE_DENIED"}
                    else:
                        result = _disp.run_task("pc_music", {"intent": record["intent"], "args": record["args"]})
                else:  # alter Direktweg (Schalter NEXUS_PHASE2_REGISTRY=0)
                    from nexus.computer.remote_executor import execute
                    from nexus.maintenance import maintenance
                    with maintenance().work("pc_remote"):
                        result = execute(record["intent"], record["args"])
        except Exception as exc:
            result = {"success": False, "error": "PC_EXECUTOR_" + type(exc).__name__}
        evidence = result.get("evidence") or {}
        if result.get("success") is True and isinstance(evidence, dict) and evidence.get("verified") is True:
            self._finish(record, "done", result.get("reply") or "Die PC-Aktion wurde geprueft ausgefuehrt.", evidence=evidence)
        else:
            self._finish(record, "failed", (result.get("reply") or "Die PC-Aktion ist fehlgeschlagen.") if not result.get("success") else
                         "Die PC-Aktion wurde nicht sicher nachgewiesen.",
                         error=result.get("error") or "PC_EXECUTION_UNVERIFIED", evidence=evidence)


def service(engine=None) -> PCRemoteService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            from nexus.nexus_os_link import publish_pc_event
            _SERVICE = PCRemoteService(STATE_DIR, engine=engine, publisher=publish_pc_event)
            _SERVICE._ensure_worker()
        elif engine is not None:
            _SERVICE.engine = engine
        return _SERVICE


def voice_confirm(body: dict, ip: str, headers: Any, engine=None) -> tuple[int, dict]:
    """Protected adapter for PC IDs on the existing /api/voice/confirm route."""
    from nexus.nexus_os_link import allowed
    if not allowed(ip, headers):
        return 403, {"success": False, "error": "NETWORK_NOT_ALLOWED"}
    principal, error = authenticate(headers)
    if error:
        return 401, {"success": False, "error": error}
    iid = body.get("request_id") or uuid.uuid4().hex
    instance = service(engine)
    with instance.lock:
        previous = instance.records.get(iid) if isinstance(iid, str) else None
        default_ts = previous["request"]["ts"] if previous else time.time()
    item = {"id": iid, "kind": "pc_command", "intent": "command.confirm",
            "args": {"confirm_id": body.get("id"), "answer": body.get("answer")},
            "source": body.get("source"), "text": body.get("answer", ""),
            "ts": body.get("ts", default_ts)}
    accepted, receipt = instance.accept(item, principal)
    return (202 if accepted else 400), {"success": accepted, "accepted": accepted, "receipt": receipt}
