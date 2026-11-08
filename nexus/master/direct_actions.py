"""Deterministische Datei-, Shell- und Diagnosepfade vor dem Agent-Loop."""
from __future__ import annotations

import re

CONTINUE = object()  # None stoppt den direkten Pfad bei einem Mehrschrittauftrag.


def _is_direct_multistep(message):
    # NEXUS_DIRECT_WRITE_MULTISTEP_GUARD_V1
    # Direkte Pfade sind nur fuer einfache Einzelaktionen.
    # Mehrschrittige Build-/Repair-Auftraege muessen in den Agent-Loop.
    _dw_text = str(message or "")
    _dw_low = _dw_text.lower()
    _dw_lines = [
        x.strip()
        for x in _dw_text.splitlines()
        if x.strip()
    ]
    _dw_numbered = sum(
        1
        for x in _dw_lines
        if (
            len(x) >= 2
            and x[0].isdigit()
            and "." in x[:4]
        )
    )
    _dw_actions = sum(
        1
        for token in (
            "write_file",
            "read_file",
            "powershell",
            "shell",
            "lösche",
            "loesche",
            "delete",
            "cleanup",
            "verifiziere",
            "verify",
            "returncode",
            "stdout",
            "stderr",
        )
        if re.search(
            r"(?<!\w)" + re.escape(token),
            _dw_low,
        )
    )
    _dw_multistep = (
        _dw_numbered >= 2
        or _dw_actions >= 2
    )
    return _dw_multistep




def _read_file(self, message, steps, run_tools):
    # NEXUS_DIRECT_READ_FILE_V1
    #
    # Explizite READ_ONLY-Dateiauftraege direkt ausfuehren.
    # Keine Pfaderfindung durch das LLM.
    _read_message = (message or "").strip()
    _read_low = _read_message.lower()

    _explicit_read = (
        (
            "lies " in _read_low
            or "lese " in _read_low
            or "read_file" in _read_low
            or "datei lesen" in _read_low
        )
        and not any(
            token in _read_low
            for token in (
                "schreibe",
                "ändere",
                "aendere",
                "verändere",
                "veraendere",
                "patch",
                "write_file",
            )
        )
    )

    if _explicit_read:
        _path_match = re.search(
            r'(?i)(?:^|\s|["\'])'
            r'((?:workspace|nexus|src|tests)[\\/]'
            r'[A-Za-z0-9_.\-\\/]+\.'
            r'(?:py|ps1|json|txt|md|yaml|yml|js|ts|tsx|html|css|sh))',
            _read_message,
        )

        if _path_match:
            _read_path = (
                _path_match.group(1)
                .replace("\\", "/")
                .strip()
            )

            _call = {
                "name": "read_file",
                "args": {
                    "path": _read_path
                }
            }

            try:
                _trace = run_tools(
                    [_call],
                    self,
                )
            except Exception as exc:
                return {
                    "success": False,
                    "reply": (
                        "BLOCKED: read_file-Executorfehler: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "intent": "file_read",
                    "plan": steps,
                    "model": None,
                    "error": "read_executor_exception",
                    "data": {
                        "tools": [],
                        "execution_status": "INCOMPLETE",
                    },
                }

            if (
                not isinstance(_trace, list)
                or len(_trace) != 1
                or not isinstance(_trace[0], dict)
            ):
                return {
                    "success": False,
                    "reply": "BLOCKED: read_file lieferte kein gueltiges Tool-Ergebnis.",
                    "intent": "file_read",
                    "plan": steps,
                    "model": None,
                    "error": "read_result_missing",
                    "data": {
                        "tools": _trace if isinstance(_trace, list) else [],
                        "execution_status": "INCOMPLETE",
                    },
                }

            _item = _trace[0]
            _result = _item.get("result")

            _ok = self._tool_succeeded(
                _result
            )

            if not isinstance(_result, dict):
                _result = {
                    "success": False,
                    "error": "ungueltiges_read_result",
                }
                _ok = False

            if _ok:
                _content = str(
                    _result.get("content") or ""
                )

                _reply = (
                    "READ_FILE PASS\n"
                    f"path: {_read_path}\n"
                    f"bytes: {_result.get('bytes')}\n"
                    "\n"
                    f"{_content}"
                )
            else:
                _reply = (
                    "READ_FILE FAIL\n"
                    f"path: {_read_path}\n"
                    f"error: {_result.get('error') or 'unbekannt'}"
                )

            return {
                "success": _ok,
                "reply": _reply,
                "intent": "file_read",
                "plan": steps,
                "model": None,
                "error": (
                    None
                    if _ok
                    else "read_file_failed"
                ),
                "data": {
                    "tools": _trace,
                    "execution_status": (
                        "TOOLS_SUCCEEDED"
                        if _ok
                        else "INCOMPLETE"
                    ),
                },
            }


def _is_agent_multistep(message):
    # NEXUS_MULTISTEP_AGENT_ROUTER_V1
    # Mehrschrittige Arbeitsauftraege duerfen NICHT als ein einziger
    # PowerShell-Befehl behandelt werden.
    _route_text = str(message or "")
    _route_low = _route_text.lower()
    _route_lines = [
        x.strip()
        for x in _route_text.splitlines()
        if x.strip()
    ]
    _numbered_steps = sum(
        1
        for x in _route_lines
        if (
            len(x) >= 2
            and x[0].isdigit()
            and "." in x[:4]
        )
    )
    _action_hits = sum(
        1
        for token in (
            "write_file",
            "read_file",
            "patch_file",
            "create_file",
            "powershell",
            "lösche",
            "loesche",
            "delete",
            "erstelle",
            "schreibe",
            "lies ",
            "verifiziere",
        )
        if re.search(
            r"(?<!\w)" + re.escape(token),
            _route_low,
        )
    )
    _multi_step_request = (
        _numbered_steps >= 2
        or (
            len(_route_lines) >= 4
            and _action_hits >= 2
        )
    )
    return _multi_step_request


def _powershell(self, message, steps):
    # NEXUS_DIRECT_POWERSHELL_V1
    #
    # Explizite PowerShell-Ausfuehrung niemals dem LLM-Router ueberlassen.
    # Befehl direkt an ShellExecutor geben und stdout/stderr/returncode
    # unveraendert zurueckgeben.
    _msg = (message or "").strip()
    _low = _msg.lower()

    _explicit_ps = (
        "powershell" in _low
        and (
            "führe" in _low
            or "fuehre" in _low
            or "ausführen" in _low
            or "ausfuehren" in _low
            or "wirklich aus" in _low
        )
    )

    if _explicit_ps:
        from datetime import datetime as _dt
        from uuid import uuid4 as _uuid4

        _run_id = (
            "RUN-"
            + _dt.now().strftime("%Y%m%d%H%M%S")
            + "-"
            + _uuid4().hex[:6]
        )

        _command = ""

        # Typischer Auftrag:
        #
        # Führe diesen PowerShell-Befehl wirklich aus:
        #
        # Write-Output ...
        # exit 0
        if "\n" in _msg:
            _parts = _msg.splitlines()

            _start = None

            for _i, _line in enumerate(_parts):
                if (
                    "powershell" in _line.lower()
                    and (
                        "aus" in _line.lower()
                        or "befehl" in _line.lower()
                    )
                ):
                    _start = _i + 1
                    break

            if _start is not None:
                _command = "\n".join(
                    _parts[_start:]
                ).strip()

        # Fallback: Inhalt nach Doppelpunkt.
        if not _command and ":" in _msg:
            _command = _msg.split(":", 1)[1].strip()

        if not _command:
            self._write_task_state(
                status="FAILED",
                message=message or "",
                intent="shell",
                error="powershell_command_missing",
                tools=[],
                run_id=_run_id,
                checkpoint="final",
                resumed=False,
            )
            return {
                "success": False,
                "reply": "BLOCKED: Kein PowerShell-Befehl im Auftrag gefunden.",
                "intent": "shell",
                "plan": steps,
                "model": None,
                "error": "powershell_command_missing",
                "data": {
                    "execution_status": "NOT_EXECUTED"
                },
            }

        try:
            _result = self.shell.run(
                _command,
                shell="powershell",
            )
        except Exception as exc:
            self._write_task_state(
                status="FAILED",
                message=message or "",
                intent="shell",
                error="shell_executor_exception",
                tools=[],
                run_id=_run_id,
                checkpoint="final",
                resumed=False,
            )
            return {
                "success": False,
                "reply": (
                    "returncode: n/a\n"
                    "stdout:\n\n"
                    "stderr:\n"
                    f"{type(exc).__name__}: {exc}"
                ),
                "intent": "shell",
                "plan": steps,
                "model": None,
                "error": "shell_executor_exception",
                "data": {
                    "execution_status": "INCOMPLETE"
                },
            }

        _rc = _result.get("returncode")

        if _rc is None:
            _rc = _result.get("exitcode")

        if _rc is None:
            _rc = _result.get("exit_code")

        _stdout = _result.get("stdout") or ""
        _stderr = _result.get("stderr") or ""

        _ok = self._tool_succeeded(_result)

        _reply = (
            f"returncode: {_rc}\n"
            f"stdout:\n{_stdout}\n"
            f"stderr:\n{_stderr}"
        )

        self._write_task_state(
            status="PASS" if _ok else "FAILED",
            message=message or "",
            intent="shell",
            error=None if _ok else (
                _result.get("error")
                or "shell_failed"
            ),
            tools=[
                {
                    "name": "powershell",
                    "ok": _ok,
                    "args": {},
                    "exitcode": _rc,
                    "restored": False,
                }
            ],
            result=(
                f"returncode={_rc}\nstdout={_stdout}"
            ),
            run_id=_run_id,
            checkpoint="final",
            resumed=False,
        )

        return {
            "success": _ok,
            "reply": _reply,
            "intent": "shell",
            "plan": steps,
            "model": None,
            "error": None if _ok else (
                _result.get("error")
                or "shell_failed"
            ),
            "data": {
                **_result,
                "execution_status": (
                    "TOOLS_SUCCEEDED"
                    if _ok
                    else "INCOMPLETE"
                ),
                "executor": "ShellExecutor",
                "shell": "powershell",
            },
        }


def _router_diagnose(self, message, intent, steps, run_tools):
    # NEXUS_DIRECT_ROUTER_DIAG_V2
    # Router-/Controller-Diagnosen laufen deterministisch und READ_ONLY.
    # Kein LLM darf dafuer Dateipfade erfinden.
    if (
        intent == "diagnose"
        and re.search(
            r"\b(tool[- ]?router|router|controller|decision|validator|quellcode)\b",
            (message or "").lower(),
            re.I,
        )
    ):
        router_files = [
            "nexus/master/controller.py",
            "nexus/master/decision.py",
            "nexus/master/validator.py",
            "nexus/master/tools.py",
        ]

        trace = []

        for router_path in router_files:
            call = {
                "name": "read_file",
                "args": {"path": router_path},
            }

            try:
                result = run_tools([call], self)
            except Exception as exc:
                result = [{
                    "name": "read_file",
                    "args": {"path": router_path},
                    "result": {
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                }]

            if isinstance(result, list):
                trace.extend(result)

        success_items = [
            item for item in trace
            if isinstance(item, dict)
            and self._tool_succeeded(item.get("result"))
        ]

        failed_items = [
            item for item in trace
            if isinstance(item, dict)
            and not self._tool_succeeded(item.get("result"))
        ]

        lines = [
            "NEXUS ROUTER DIAGNOSE · READ_ONLY",
            f"Dateien gelesen: {len(success_items)}/{len(router_files)}",
        ]

        for item in trace:
            name = str(item.get("name") or "?")
            args = item.get("args") or {}
            target = str(args.get("path") or "?")
            result = item.get("result") or {}

            if self._tool_succeeded(result):
                lines.append(f"PASS · {name} · {target}")
            else:
                err = (
                    result.get("error")
                    if isinstance(result, dict)
                    else str(result)
                )
                lines.append(
                    f"FAIL · {name} · {target} · {err}"
                )

        lines.append("Schreibaktionen: 0")
        lines.append("system_monitor: 0")

        return {
            "success": len(failed_items) == 0,
            "reply": "\n".join(lines),
            "intent": intent,
            "plan": steps,
            "model": None,
            "error": (
                None
                if not failed_items
                else "router_diag_read_failed"
            ),
            "data": {
                "tools": trace,
                "execution_status": (
                    "TOOLS_SUCCEEDED"
                    if not failed_items
                    else "INCOMPLETE"
                ),
            },
        }


def route_direct(self, message: str, intent: str, steps: list, run_tools):
    if _is_direct_multistep(message):
        return None
    result = _read_file(self, message, steps, run_tools)
    if result is not None:
        return result
    if _is_agent_multistep(message):
        return None
    result = _powershell(self, message, steps)
    if result is not None:
        return result
    result = _router_diagnose(self, message, intent, steps, run_tools)
    return CONTINUE if result is None else result
