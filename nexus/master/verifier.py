"""Belegbasierte Abschlussprüfung ohne Controller-Zustand."""
from __future__ import annotations


def verify_goal_completion(message: str, intent: str, tool_trace, tool_succeeded):
    """
    Konservative Abschlusspruefung.
    PASS nur, wenn reale Tool-Nachweise zum Benutzerziel passen.
    """
    msg = (message or "").lower()
    intent_low = (intent or "").lower()

    trace = [
        x for x in (tool_trace or [])
        if isinstance(x, dict)
    ]

    def _name(item):
        return str(
            item.get("name") or ""
        ).lower()

    def _ok(item):
        return tool_succeeded(
            item.get("result")
        )

    successful = [
        x for x in trace if _ok(x)
    ]

    failed = [
        x for x in trace if not _ok(x)
    ]

    names_ok = [
        _name(x) for x in successful
    ]

    # Ein real ausgefuehrter Agent-Auftrag darf bei vorhandenen
    # Tool-Fehlern nicht einfach PASS werden.
    if failed:
        return (
            False,
            "tool_failed_or_unconfirmed",
        )

    wants_read = any(
        token in msg
        for token in (
            "lies ",
            "lesen",
            "read_file",
            "zeige datei",
            "datei anzeigen",
            "prüfe datei",
            "pruefe datei",
            "untersuche datei",
        )
    )

    wants_write = any(
        token in msg
        for token in (
            "schreibe",
            "schreib ",
            "write_file",
            "erstelle datei",
            "create_file",
            "patch_file",
            "ändere",
            "aendere",
            "repariere",
            "reperiere",
            "fix ",
            "fixe ",
        )
    )

    wants_exec = any(
        token in msg
        for token in (
            "powershell",
            "shell",
            "führe aus",
            "fuehre aus",
            "ausführen",
            "ausfuehren",
            "starte ",
            "run_script",
            "python_exec",
        )
    )

    wants_verify = any(
        token in msg
        for token in (
            "verifiziere",
            "verify",
            "prüfe danach",
            "pruefe danach",
            "kontrolliere",
            "exakt",
            "nachweis",
            "belege",
        )
    )

    wants_repair = any(
        token in msg
        for token in (
            "repariere",
            "reperiere",
            "behebe",
            "fixe",
            "fix ",
            "patch",
        )
    )

    wants_status = any(
        token in msg
        for token in (
            "status",
            "health",
            "systemzustand",
            "systemstatus",
            "monitor",
        )
    )

    # ----------------------------------------------------
    # READ-Ziel
    # ----------------------------------------------------
    if wants_read and not wants_write:
        if "read_file" not in names_ok:
            return (
                False,
                "goal_read_not_proven",
            )

    # ----------------------------------------------------
    # WRITE-/REPAIR-Ziel
    # ----------------------------------------------------
    if wants_write or wants_repair:
        write_tools = (
            "write_file",
            "patch_file",
            "create_file",
        )

        write_positions = [
            i
            for i, item in enumerate(trace)
            if _name(item) in write_tools
            and _ok(item)
        ]

        exec_positions = [
            i
            for i, item in enumerate(trace)
            if _name(item) in (
                "powershell",
                "shell",
                "run_script",
                "python_exec",
            )
            and _ok(item)
        ]

        # Reparatur muss mindestens eine reale Veraenderung oder
        # reale ausfuehrende Aktion enthalten.
        if (
            wants_repair
            and not write_positions
            and not exec_positions
        ):
            return (
                False,
                "goal_repair_not_proven",
            )

        if (
            wants_write
            and not write_positions
            and not wants_repair
        ):
            return (
                False,
                "goal_write_not_proven",
            )

        # Falls Verifikation verlangt wurde:
        # nach letzter Schreibaktion muss ein erfolgreicher Read folgen.
        if wants_verify and write_positions:
            last_write = max(
                write_positions
            )

            verified_after = any(
                i > last_write
                and _name(item) == "read_file"
                and _ok(item)
                for i, item
                in enumerate(trace)
            )

            if not verified_after:
                return (
                    False,
                    "goal_write_verify_not_proven",
                )

    # ----------------------------------------------------
    # EXECUTE-Ziel
    # ----------------------------------------------------
    if wants_exec:
        executable = (
            "powershell",
            "shell",
            "run_script",
            "python_exec",
        )

        if not any(
            n in executable
            for n in names_ok
        ):
            return (
                False,
                "goal_execution_not_proven",
            )

    # ----------------------------------------------------
    # STATUS-Ziel
    # ----------------------------------------------------
    if wants_status:
        if not any(
            n in (
                "status",
                "system_monitor",
                "powershell",
                "shell",
            )
            for n in names_ok
        ):
            return (
                False,
                "goal_status_not_proven",
            )

    # ----------------------------------------------------
    # Allgemeiner Agent-Auftrag
    # ----------------------------------------------------
    execution_intents = (
        "build",
        "repair",
        "execute",
        "write",
        "self_improve",
        "diagnose",
    )

    if (
        any(
            token in intent_low
            for token in execution_intents
        )
        and not successful
    ):
        return (
            False,
            "goal_no_real_tool_evidence",
        )

    return (
        True,
        None,
    )
