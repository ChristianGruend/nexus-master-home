"""Kontrollierte lokale GPT-Hermes-Bruecke fuer NEXUS."""

import json
import logging

log = logging.getLogger("nexus.gpt_hermes_bridge")

ROUTER_PROMPT = """
Du bist der Aufgaben-Router von NEXUS GPT.
Entscheide, ob die Nutzeranfrage echte Aktionen an Dateien,
Code, Projekten oder Werkzeugen erfordert.

delegate=true nur bei konkreten Arbeitsauftraegen wie:
Dateien lesen, erstellen oder bearbeiten, programmieren,
Code analysieren, Tests ausfuehren, Fehler reparieren,
Projektstrukturen untersuchen oder technische Aufgaben ausfuehren.

delegate=false bei normalen Fragen, Erklaerungen,
Beratung, Ideen, Unterhaltung und allgemeinen Gespraechen.

Keine Aktion ausfuehren. Nur entscheiden.
Antworte ausschliesslich als JSON:
{"delegate": true, "reason": "kurzer Grund"}
oder
{"delegate": false, "reason": "kurzer Grund"}
"""

SUMMARY_PROMPT = """
Du bist NEXUS GPT, der zentrale Ansprechpartner.
Fasse ausschliesslich die tatsaechlich von Hermes gelieferten
Ergebnisse zusammen. Erfinde keine ausgefuehrten Aktionen,
Dateien, Tests oder Erfolge.
Unterscheide klar zwischen erfolgreich, teilweise erfolgreich
und fehlgeschlagen. Antworte kurz und auf Deutsch.
"""


def ask_with_hermes(message: str, workdir: str | None = None) -> dict:
    """GPT entscheidet; Hermes fuehrt freigegebene Arbeitsauftraege aus."""

    text = (message or "").strip()
    if not text:
        return {
            "success": False,
            "agent": "gpt",
            "error": "MESSAGE_REQUIRED",
            "reply": "Keine Nachricht angegeben."
        }

    from nexus.integrations.gpt_local_agent import ask as gpt_ask

    # 1. GPT entscheidet ueber Delegation.
    decision = gpt_ask(text, fmt="json", system=ROUTER_PROMPT)

    if not decision.get("success"):
        return {
            "success": False,
            "agent": "gpt",
            "error": decision.get("error") or "GPT_ROUTER_UNAVAILABLE",
            "reply": "Die Aufgabenentscheidung durch GPT ist fehlgeschlagen. "
                     "Es wurde keine Hermes-Aktion gestartet."
        }

    try:
        choice = json.loads(decision.get("reply") or "{}")
        delegate = choice.get("delegate") is True
    except (ValueError, TypeError, AttributeError):
        delegate = False

    # 2. Normaler Chat bleibt bei GPT.
    if not delegate:
        result = gpt_ask(text)
        return {
            **result,
            "agent": "gpt",
            "source": "gpt",
            "execution_status": "GPT_DIRECT_CHAT",
            "delegated": False
        }

    # 3. Konkrete Arbeitsauftraege an Hermes.
    from nexus.integrations.hermes_agent import ask as hermes_ask

    try:
        if workdir:
            hermes = hermes_ask(text, workdir=workdir)
        else:
            hermes = hermes_ask(text)
    except Exception as exc:
        log.exception("Hermes-Aufruf fehlgeschlagen")
        return {
            "success": False,
            "agent": "gpt",
            "source": "gpt_hermes_bridge",
            "delegated": True,
            "error": "HERMES_CALL_FAILED",
            "reply": f"Hermes konnte nicht ausgefuehrt werden: {exc}"
        }

    # 4. Ausstehende Freigaben explizit an GPT/NEXUS weitergeben.
    _approval_data = hermes.get("data")
    if not isinstance(_approval_data, dict):
        _approval_data = {}

    _approval_calls = _approval_data.get("tool_calls") or []
    _approval_info = {}

    for _call in _approval_calls:
        if not isinstance(_call, dict):
            continue
        if _call.get("status") != "APPROVAL_REQUIRED":
            continue

        _preview = _call.get("result_preview") or ""
        if isinstance(_preview, dict):
            _parsed = _preview
        else:
            try:
                _parsed = json.loads(str(_preview))
            except (ValueError, TypeError):
                _parsed = {}

        if isinstance(_parsed, dict):
            _approval_info.update(_parsed)

        break

    _approval_pending = (
        _approval_data.get("execution_status") == "HERMES_APPROVAL_REQUIRED"
        or any(
            isinstance(_c, dict)
            and _c.get("status") == "APPROVAL_REQUIRED"
            for _c in _approval_calls
        )
    )

    if _approval_pending:
        _approval_id = _approval_info.get("approval_id")
        _approval_reply = (
            _approval_info.get("message")
            or "Für diesen Vorgang ist eine ausdrückliche Freigabe erforderlich. "
               "Bis zur Freigabe wurde keine Änderung als abgeschlossen bestätigt."
        )
        return {
            "success": False,
            "agent": "gpt",
            "source": "gpt_hermes_bridge",
            "delegated": True,
            "execution_status": "HERMES_APPROVAL_REQUIRED",
            "error": "APPROVAL_REQUIRED",
            "approval_required": True,
            "approval_id": _approval_id,
            "reply": _approval_reply,
            "hermes": hermes,
            "data": {
                "execution_status": "HERMES_APPROVAL_REQUIRED",
                "approval_id": _approval_id,
                "approval_details": _approval_info,
                "tool_calls": _approval_calls
            }
        }

    # 5. Keine Erfolgsmeldung bei fehlgeschlagener Ausfuehrung.
    if not hermes.get("success"):
        return {
            "success": False,
            "agent": "gpt",
            "source": "gpt_hermes_bridge",
            "delegated": True,
            "execution_status": (
                hermes.get("data", {}).get("execution_status")
                or hermes.get("error")
                or "HERMES_FAILED"
            ),
            "error": hermes.get("error") or "HERMES_FAILED",
            "reply": hermes.get("reply") or
                     "Hermes hat den Auftrag nicht erfolgreich abgeschlossen.",
            "hermes": hermes,
            "data": hermes.get("data", {})
        }

    # NEXUS_VERIFIED_HERMES_EXECUTION_20261103
    _data = hermes.get("data") if isinstance(hermes.get("data"), dict) else {}
    _calls = _data.get("tool_calls") or []
    _status = _data.get("execution_status")
    _provenance = (
        hermes.get("model_provenance")
        or _data.get("model_provenance")
    )
    _verified = (
        _status == "HERMES_TOOLS_EXECUTED"
        and _provenance == "model_response_tools_executed"
        and bool(_calls)
        and all(isinstance(c, dict) and c.get("ok") is True for c in _calls)
    )

    if not _verified:
        return {
            "success": False,
            "agent": "gpt",
            "source": "gpt_hermes_bridge",
            "delegated": True,
            "execution_status": "HERMES_EXECUTION_UNVERIFIED",
            "error": "HERMES_EXECUTION_UNVERIFIED",
            "reply": (
                "Hermes hat keine vollstaendig nachweisbare Tool-Ausfuehrung "
                "gemeldet. Der Auftrag wird deshalb nicht als erledigt markiert. "
                "Es wurde keine erfolgreiche Ausfuehrung bestaetigt."
            ),
            "hermes": hermes,
            "data": _data
        }

    # 5. GPT formuliert die Antwort anhand des echten Hermes-Ergebnisses.
    evidence = {
        "success": hermes.get("success"),
        "execution_status": hermes.get("data", {}).get("execution_status"),
        "reply": hermes.get("reply"),
        "tool_calls": hermes.get("data", {}).get("tool_calls", []),
        "request_id": hermes.get("request_id")
    }

    summary = gpt_ask(
        "Nutzerauftrag:\n" + text +
        "\n\nTatsaechliches Hermes-Ergebnis:\n" +
        json.dumps(evidence, ensure_ascii=False, default=str)[:12000],
        system=SUMMARY_PROMPT
    )

    reply = (
        summary.get("reply")
        if summary.get("success") and summary.get("reply")
        else hermes.get("reply")
    )

    return {
        "success": True,
        "agent": "gpt",
        "source": "gpt_hermes_bridge",
        "delegated": True,
        "execution_status": "HERMES_EXECUTION_SUCCESS",
        "reply": reply,
        "hermes": hermes,
        "data": {
            "execution_status": "HERMES_EXECUTION_SUCCESS",
            "hermes_request_id": hermes.get("request_id"),
            "tool_calls": hermes.get("data", {}).get("tool_calls", []),
            "summary_generated": bool(summary.get("success"))
        }
    }