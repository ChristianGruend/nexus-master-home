"""Rein lesende Filter für modellierte Tool-Aufrufe vor der Ausführung."""
from __future__ import annotations


def _basic_filter(calls, message, technical_diag, _executed_signatures):
    # NEXUS_TOOL_SCOPE_GUARD_V1
    # Ungefragte Health-/Monitor-Aufrufe aus normalen Arbeitsauftraegen
    # entfernen. Master-Prompt darf nur bei ausdruecklichem Auftrag
    # veraendert werden.
    _request_low = (message or "").lower()

    _health_requested = any(
        token in _request_low
        for token in (
            "status",
            "systemstatus",
            "system status",
            "monitor",
            "health",
            "cpu",
            "ram",
            "arbeitsspeicher",
            "gpu",
            "auslastung",
        )
    )

    if not _health_requested:
        calls = [
            c for c in calls
            if str(c.get("name") or "").lower()
            not in ("status", "system_monitor")
        ]

    _master_prompt_explicit = any(
        token in _request_low
        for token in (
            "master_prompt_runtime.txt",
            "master prompt",
            "master-prompt",
        )
    )

    if not _master_prompt_explicit:
        calls = [
            c for c in calls
            if not (
                str(c.get("name") or "").lower()
                in ("write_file", "patch_file", "create_file")
                and "master_prompt_runtime.txt"
                in str((c.get("args") or {}).get("path") or "").lower()
            )
        ]

    # NEXUS_CROSS_ROUND_TOOL_DEDUPE_V1
    #
    # Identischen Tool-Aufruf nicht in spaeteren Agent-Runden
    # erneut ausfuehren. Das verhindert Endlosschleifen wie
    # write -> read -> write derselben Daten oder wiederholtes
    # status/system_monitor.
    _unique_calls = []

    for _candidate in calls:
        if not isinstance(_candidate, dict):
            continue

        _candidate_name = str(
            _candidate.get("name") or ""
        ).lower()

        _candidate_args = (
            _candidate.get("args")
            if isinstance(
                _candidate.get("args"),
                dict
            )
            else {}
        )

        try:
            import json as _json

            _candidate_key = (
                _candidate_name,
                _json.dumps(
                    _candidate_args,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
            )
        except Exception:
            _candidate_key = (
                _candidate_name,
                repr(_candidate_args),
            )

        if _candidate_key in _executed_signatures:
            continue

        _unique_calls.append(
            _candidate
        )

    calls = _unique_calls

    # NEXUS_SINGLE_MUTATION_PER_ROUND_V1
    #
    # Pro Agent-Runde nur eine zustandsveraendernde Dateiaktion.
    # Nach einem Write muss der Agent erst den echten Zustand lesen/
    # verifizieren, bevor eine weitere Mutation erlaubt wird.
    _mutation_tools = {
        "write_file",
        "patch_file",
        "create_file",
    }

    _mutation_seen = False
    _single_mutation_calls = []

    for _sm_call in calls:
        if not isinstance(_sm_call, dict):
            continue

        _sm_name = str(
            _sm_call.get("name") or ""
        ).lower()

        if _sm_name in _mutation_tools:
            if _mutation_seen:
                continue

            _mutation_seen = True

        _single_mutation_calls.append(
            _sm_call
        )

    calls = _single_mutation_calls


    if technical_diag:
        allowed_technical_tools = {"read_file", "list_dir"}
        calls = [
            c for c in calls
            if str(c.get("name") or "").lower() in allowed_technical_tools
        ]
    return calls


def _scope_filter(calls, message):
    # NEXUS_CENTRAL_TOOL_SCOPE_V1
    #
    # Letzte verbindliche Schranke direkt vor der Ausfuehrung.
    # Ein vom Modell vorgeschlagenes Tool ist nicht automatisch erlaubt.
    _scope_low = (message or "").lower()
    _allow_health = any(
        token in _scope_low
        for token in (
            "status",
            "systemstatus",
            "system status",
            "health",
            "monitor",
            "cpu",
            "ram",
            "arbeitsspeicher",
            "gpu",
            "auslastung",
            "leistung",
        )
    )
    _allow_write = any(
        token in _scope_low
        for token in (
            "schreibe",
            "schreib",
            "erstelle",
            "erzeuge",
            "ändere",
            "aendere",
            "verändere",
            "veraendere",
            "patch",
            "fix",
            "repariere",
            "reperiere",
            "baue",
            "write_file",
            "patch_file",
            "create_file",
        )
    )
    _allow_exec = any(
        token in _scope_low
        for token in (
            "powershell",
            "shell",
            "befehl",
            "command",
            "führe aus",
            "fuehre aus",
            "ausführen",
            "ausfuehren",
            "run_script",
            "python_exec",
            "starte",
            "installiere",
            "baue",
            "repariere",
            "reperiere",
            "fix",
            "patch",
        )
    )
    _master_prompt_explicit = any(
        token in _scope_low
        for token in (
            "master_prompt_runtime.txt",
            "master prompt",
            "master-prompt",
        )
    )

    _scoped_calls = []

    for _scope_call in calls:
        if not isinstance(_scope_call, dict):
            continue

        _scope_name = str(
            _scope_call.get("name") or ""
        ).lower()

        _scope_args = (
            _scope_call.get("args")
            if isinstance(
                _scope_call.get("args"),
                dict,
            )
            else {}
        )

        _scope_path = str(
            _scope_args.get("path") or ""
        ).lower()

        # Health nur bei ausdruecklicher Health-/Statusabsicht.
        if (
            _scope_name in (
                "status",
                "system_monitor",
            )
            and not _allow_health
        ):
            continue

        # Schreibtools nur bei echter Schreibabsicht.
        if (
            _scope_name in (
                "write_file",
                "patch_file",
                "create_file",
            )
            and not _allow_write
        ):
            continue

        # Master-Prompt niemals nebenbei veraendern.
        if (
            _scope_name in (
                "write_file",
                "patch_file",
                "create_file",
            )
            and "master_prompt_runtime.txt"
            in _scope_path
            and not _master_prompt_explicit
        ):
            continue

        # Ausfuehrungstools nur bei echter Execute-/Repair-Absicht.
        if (
            _scope_name in (
                "powershell",
                "shell",
                "run_script",
                "python_exec",
            )
            and not _allow_exec
        ):
            continue

        _scoped_calls.append(
            _scope_call
        )

    calls = _scoped_calls
    return calls


def _order_filter(calls, _goal_required, _goal_completed_families):

    # NEXUS_GOAL_ORDER_GUARD_V1
    #
    # Verbindliche Reihenfolge fuer mehrstufige Auftraege:
    # WRITE -> READ -> EXEC/VERIFY -> CLEANUP
    _order_completed = (
        _goal_completed_families()
        if "_goal_completed_families" in locals()
        else set()
    )

    _ordered_calls = []

    for _order_call in calls:
        if not isinstance(_order_call, dict):
            continue

        _order_name = str(
            _order_call.get("name") or ""
        ).lower()

        _order_args = (
            _order_call.get("args")
            if isinstance(
                _order_call.get("args"),
                dict,
            )
            else {}
        )

        _order_command = str(
            _order_args.get("command")
            or _order_args.get("script")
            or _order_args.get("code")
            or ""
        ).lower()

        _is_write = (
            _order_name in (
                "write_file",
                "patch_file",
                "create_file",
            )
        )

        _is_read = (
            _order_name == "read_file"
        )

        _is_exec = (
            _order_name in (
                "powershell",
                "shell",
                "run_script",
                "python_exec",
            )
        )

        _is_cleanup = (
            _order_name in (
                "delete_file",
                "remove_file",
            )
            or (
                _is_exec
                and any(
                    token in _order_command
                    for token in (
                        "remove-item",
                        "del ",
                        "erase ",
                        "unlink",
                        "os.remove",
                    )
                )
            )
        )

        # WRITE muss zuerst kommen.
        if (
            "write" in _goal_required
            and "write" not in _order_completed
            and not _is_write
        ):
            continue

        # READ muss nach WRITE kommen.
        if (
            "read" in _goal_required
            and "write" in _goal_required
            and "write" in _order_completed
            and "read" not in _order_completed
            and not _is_read
        ):
            continue

        # EXEC/VERIFY erst nach READ.
        if (
            "exec" in _goal_required
            and "read" in _goal_required
            and "read" not in _order_completed
            and _is_exec
        ):
            continue

        # CLEANUP wirklich zuletzt.
        if _is_cleanup:
            _pre_cleanup_required = (
                _goal_required
                - {"cleanup"}
            )

            if not _pre_cleanup_required.issubset(
                _order_completed
            ):
                continue

        _ordered_calls.append(
            _order_call
        )

    calls = _ordered_calls
    return calls


def filter_tool_calls(calls, message, technical_diag, _executed_signatures, _goal_required, _goal_completed_families):
    calls = _basic_filter(calls, message, technical_diag, _executed_signatures)
    calls = _scope_filter(calls, message)
    return _order_filter(calls, _goal_required, _goal_completed_families)
