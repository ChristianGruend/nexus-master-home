"""WhatsApp access from NEXUS Master Home through the existing phone bridge.

No mobile component is changed. Sending uses the already registered ADB
WhatsApp flow and therefore needs an unlocked, connected phone. Reading returns
only the currently visible WhatsApp screen, not the full chat history.
"""
from __future__ import annotations

import hashlib
import os
import xml.etree.ElementTree as ET
from typing import Any


def _approval_required() -> bool:
    return (os.environ.get("NEXUS_CLAUDE_WHATSAPP_APPROVAL", "1") or "1").strip() != "0"


def _gate(label: str, approval_id: str | None) -> dict[str, Any] | None:
    if not _approval_required():
        return None
    from nexus.terminal import guard
    verdict = guard.authorize("Claude", label, shell="session", approval_id=approval_id,
                              force_approval=True)
    if verdict.get("allowed"):
        return None
    guard.audit("whatsapp_blocked", agent="Claude", label=label,
                approval_id=verdict.get("approval_id"), status=verdict.get("status"))
    return {"success": False, "status": verdict.get("status"),
            "error": verdict.get("error") or verdict.get("status") or "whatsapp_approval_required",
            "approval_id": verdict.get("approval_id"), "action": "whatsapp", "verified": False}


def _resolve(number: str | None, contact: str | None, serial: str | None) -> tuple[str | None, dict[str, Any] | None]:
    from nexus import phone_actions as pa
    if number:
        return str(number).strip(), None
    if not contact:
        return None, {"success": False, "error": "whatsapp_recipient_missing",
                      "reply": "Nummer oder Kontaktname fehlt.", "action": "whatsapp"}
    found = pa.resolve_contact(str(contact), serial=serial)
    if not found.get("unique") or not found.get("matches"):
        return None, {"success": False, "error": "whatsapp_contact_ambiguous",
                      "matches": found.get("matches") or [],
                      "reply": "Kontakt nicht eindeutig gefunden.", "action": "whatsapp"}
    return str(found["matches"][0]["number"]), None


def read_visible(*, limit: int = 60) -> dict[str, Any]:
    """Read text nodes currently visible on the phone's WhatsApp screen."""
    from nexus import phone_actions as pa
    dev = pa.select_device()
    if dev.get("status") != "OK":
        return {"success": False, "status": "UNAVAILABLE", "error": dev.get("reason") or dev.get("status"),
                "reply": dev.get("reply"), "action": "whatsapp_read", "verified": False}
    serial = dev.get("serial")
    screen = pa.screen_state(serial)
    if not screen.get("unlocked"):
        return {"success": False, "error": "phone_locked", "action": "whatsapp_read",
                "reply": "Das Handy ist gesperrt; WhatsApp kann nicht gelesen werden.",
                "verified": False}
    xml_text = pa._ui_dump(serial)  # existing read-only helper
    if not xml_text:
        return {"success": False, "error": "whatsapp_ui_unavailable", "action": "whatsapp_read",
                "verified": False}
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return {"success": False, "error": "whatsapp_ui_parse_failed", "action": "whatsapp_read",
                "verified": False}
    texts: list[str] = []
    packages = set(pa.WHATSAPP_PACKAGES)
    for node in root.iter("node"):
        if (node.get("package") or "") not in packages:
            continue
        for attr in ("text", "content-desc"):
            value = (node.get(attr) or "").strip()
            if value and value not in texts:
                texts.append(value)
        if len(texts) >= max(1, min(int(limit or 60), 200)):
            break
    return {"success": True, "action": "whatsapp_read", "serial": serial,
            "texts": texts, "count": len(texts), "verified": True,
            "note": "visible_screen_only", "reply": "Sichtbare WhatsApp-Texte gelesen."}


def send(number: str | None = None, text: str | None = None, contact: str | None = None,
         recipient: str = "", approval_id: str | None = None) -> dict[str, Any]:
    from nexus import phone_actions as pa
    if not (text or "").strip():
        return {"success": False, "error": "whatsapp_text_missing", "action": "whatsapp_send"}
    if len(text) > 2000:
        return {"success": False, "error": "whatsapp_text_too_long", "action": "whatsapp_send"}
    dev = pa.select_device()
    serial = dev.get("serial") if dev.get("status") == "OK" else None
    resolved, err = _resolve(number, contact, serial)
    if err:
        return err
    label = ("whatsapp.send(number=" + str(resolved) + ",sha256="
             + hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12] + ")")
    blocked = _gate(label, approval_id)
    if blocked:
        return blocked
    out = pa.whatsapp_send(str(resolved), str(text), source="claude",
                           recipient=recipient or contact or "", confirmed=True)
    out["action"] = "whatsapp_send"
    out["number"] = resolved
    return out


def open_chat(number: str | None = None, text: str | None = None, contact: str | None = None,
              recipient: str = "") -> dict[str, Any]:
    from nexus import phone_actions as pa
    dev = pa.select_device()
    serial = dev.get("serial") if dev.get("status") == "OK" else None
    resolved, err = _resolve(number, contact, serial)
    if err:
        return err
    out = pa.whatsapp_open_chat(str(resolved), str(text or ""), source="claude",
                                recipient=recipient or contact or "")
    out["action"] = "whatsapp_open"
    out["number"] = resolved
    return out
