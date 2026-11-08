"""Agent-Loop: LLM-Antworten und reale Tool-Nachweise als Fortschrittsstrom."""
from __future__ import annotations

import re

from nexus.master.tools import format_tool_result, parse_tool_calls
from nexus.master.validator import validate_reply
from nexus.master.tool_scope import filter_tool_calls

_CODE_DUMP_RE = re.compile(
    r"```(?:python|py|javascript|js|typescript|ts|tsx|powershell|ps1|bash|sh|html|css|json|java|go|rust)\b",
    re.I,
)


def execute_stream(self, message: str, session_messages: list, intent: str, steps: list, decision: dict | None, run_tools):
    """Wie Agent-Loop, yield progress/token zwischen Tool-Runden für Live-Chat."""
    decision = decision or {}
    timeout_hint = int(decision.get("timeout_hint") or 600)
    script_hint = decision.get("script_hint")
    self_build = bool(decision.get("self_build")) or intent in ("build", "shell", "run_script", "execute")
    build_extra = (
        f" Build/npm/vite: timeout bis {timeout_hint}s via args.timeout. "
        "Echte Builds ausschliesslich entsprechend der konkreten Benutzeranweisung ausfuehren. "
        "Kein Default-Projekt, kein Default-Skript, keinen Projektnamen und keinen Pfad erfinden. "
    )
    if script_hint:
        build_extra += f' Nutze run_script path={script_hint} timeout={timeout_hint}.'
    progress_rule = (
        " TOOL-REGEL: Die konkrete Benutzeranweisung bestimmt das Tool. "
        "Explizite Pfade, Inhalte und Verbote haben Vorrang. "
        "READ_ONLY, Diagnose, Suche und Analyse duerfen keine Schreibaktion ausloesen. "
        "write_file nur bei tatsaechlicher Schreibabsicht verwenden. "
        "Keine Pfade, Projekte, Skripte oder Inhalte erfinden. "
        "Nach TOOL_RESULT weiterarbeiten oder knapp final antworten. Keine Simulation."
    )
    technical_diag = (
        intent == "diagnose"
        and bool(re.search(
            r"\b(tool[- ]?router|router|controller|decision|validator|quellcode|code|datei|funktion|read[_ -]?only)\b",
            (message or "").lower(),
            re.I,
        ))
    )

    tech_diag_rule = (
        " TECHNISCHE READ_ONLY-DIAGNOSE: Ausschliesslich read_file/list_dir verwenden. "
        "KEIN system_monitor, write_file, patch_file, create_file, shell oder powershell. "
        "Untersuche reale Projektdateien; keine Systemmetriken ausgeben. "
        if technical_diag else ""
    )

    tool_hint = (
        "Antworte auf Deutsch. Bei Build/Shell/Dateien/Skripten: echte Tools nutzen. Format: "
        'Genau ein JSON-Objekt mit name und args zwischen <tool_call> und </tool_call>. '
        "Waehle das Tool passend zur Benutzerabsicht. Keine Pfade, Dateinamen oder Inhalte erfinden. "
        "oder ```tool\\n{\"name\":\"read_file\",\"args\":{\"path\":\"...\"}}\\n``` "
        "Erlaubte name: powershell, shell, run_script, python_exec, read_file, write_file, list_dir, status, system_monitor, open_url, open_path, launch_app, play_media, list_dir_os, close_opened, web_search, youtube_search, youtube_play, volume. Beispiel open_url/launch_app/open_path/play_media fuer PC-Steuerung. "
        "NEXUS-Root = cwd. Nach TOOL_RESULT weiterarbeiten. Finalantwort ohne Tool wenn fertig. "
        "NIEMALS Befehlsausgaben erfinden. Konkrete Ergebnisse statt Fluff."
        + build_extra
        + tech_diag_rule
        + (progress_rule if self_build else "")
    )
    msgs = self.build_context(
        session_messages,
        extra=tool_hint,
        include_project_knowledge=self_build,
    )
    msgs.append({"role": "user", "content": message})

    # NEXUS_ACTIVE_GOAL_GUARD_V1
    #
    # Der Originalauftrag bleibt waehrend aller Tool-Runden
    # die verbindliche Aufgabe. Tool-Ergebnisse duerfen das
    # Ziel praezisieren, aber nicht ersetzen.
    _active_goal = (message or "").strip()

    _goal_guard = (
        "AKTIVER AUFTRAG — VERBINDLICH BIS ABSCHLUSS:\n"
        + _active_goal
        + "\n\n"
        "REGELN:\n"
        "- Verliere diesen Auftrag zwischen Tool-Runden nicht.\n"
        "- Fuehre nur Schritte aus, die fuer diesen Auftrag notwendig sind.\n"
        "- Keine zusaetzlichen Health-, Status-, Monitor- oder Diagnoseaktionen, "
        "ausser der Auftrag verlangt sie ausdruecklich.\n"
        "- Keine Dateien veraendern, die fuer den Auftrag nicht erforderlich sind.\n"
        "- Keine Master-Prompt-Aenderung ohne ausdruecklichen Auftrag.\n"
        "- Erfolgreich erledigte identische Tool-Schritte nicht wiederholen.\n"
        "- Fehlgeschlagene Schritte nicht blind identisch wiederholen; Ursache "
        "ermitteln oder alternativen realen Schritt waehlen.\n"
        "- PASS nur mit realem Executor-Nachweis.\n"
        "- Sobald der Auftrag vollstaendig belegt erledigt ist: STOP."
    )

    msgs.append({
        "role": "system",
        "content": _goal_guard,
    })

    # NEXUS_CHECKPOINT_RESTORE_V1
    #
    # Resume desselben Auftrags uebernimmt bereits real ausgefuehrte
    # Schritte aus dem gespeicherten Task-State. Idempotenz: identische
    # Schritte werden nicht erneut an den Executor uebergeben.
    def _sig_key(_k_name, _k_args):
        try:
            import json as _json

            return (
                str(_k_name or "").lower(),
                _json.dumps(
                    _k_args or {},
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
            )
        except Exception:
            return (
                str(_k_name or "").lower(),
                repr(_k_args or {}),
            )

    _restored_trace: list[dict] = []
    _is_resume = False
    _saved_state = self._read_task_state()

    # NEXUS_LEGACY_STATE_TRUST_V1
    #
    # Ein gespeicherter Zustand, der sich selbst als ungeprueft
    # (provenance) oder fortsetzungsunfaehig (resume_allowed) markiert,
    # ist vollstaendig inert: er liefert weder Zielnachweise noch
    # Erfolgsbelege noch Ausfuehrungsvermerke. Der Auftrag laeuft dann
    # wie ohne Zustand neu und darf nur mit echten Ergebnissen bestehen.
    _state_trusted = True
    if isinstance(_saved_state, dict):
        if _saved_state.get("resume_allowed") is False:
            _state_trusted = False
        elif (
            str(_saved_state.get("provenance") or "")
            == "legacy_tool_summary_unverified"
        ):
            _state_trusted = False

    if (
        isinstance(_saved_state, dict)
        and str(_saved_state.get("task") or "").strip()
        == (message or "").strip()
        and str(_saved_state.get("status") or "").upper()
        in ("ACTIVE", "PARTIAL", "BLOCKED")
    ):
        _is_resume = True

        for _rs_item in (
            _saved_state.get("tools") or [] if _state_trusted else []
        ):
            if not isinstance(_rs_item, dict):
                continue

            _rs_name = str(_rs_item.get("name") or "").strip()
            if not _rs_name:
                continue

            _rs_ok = bool(_rs_item.get("ok"))

            if _rs_item.get("error"):
                _rs_result = {
                    "success": False,
                    "error": str(_rs_item.get("error")),
                }
            else:
                _rs_result = {"success": _rs_ok}

            _restored_trace.append({
                "name": _rs_name,
                "args": (
                    _rs_item.get("args")
                    if isinstance(_rs_item.get("args"), dict)
                    else {}
                ),
                "result": _rs_result,
                "restored": True,
                "run_id": str(
                    _saved_state.get("run_id") or ""
                ),
            })

    _executed_signatures = set()

    for _seed_item in _restored_trace:
        if self._tool_succeeded(_seed_item.get("result")):
            _executed_signatures.add(
                _sig_key(
                    _seed_item.get("name"),
                    _seed_item.get("args"),
                )
            )

    # NEXUS_REPAIR_RETRY_GUARD_V1
    # Gleiche Fehlerursache darf nur begrenzt erneut bearbeitet werden.
    _repair_failure_counts = {}
    _max_repair_attempts = 2

    # NEXUS_GOAL_CONTRACT_V1
    _goal_text = (message or "").lower()
    _goal_contract_reprompts = 0
    _goal_contract_max_reprompts = 3

    _goal_required = set()

    if any(
        token in _goal_text
        for token in (
            "schreibe",
            "schreib ",
            "write_file",
            "erstelle die datei",
            "create_file",
            "patch_file",
        )
    ):
        _goal_required.add("write")

    if any(
        token in _goal_text
        for token in (
            "lies ",
            "lese ",
            "read_file",
            "wieder ein",
            "verifiziere",
            "verify",
            "kontrolliere",
        )
    ):
        _goal_required.add("read")

    if any(
        token in _goal_text
        for token in (
            "powershell",
            "shell",
            "führe danach",
            "fuehre danach",
            "unabhängige prüfung",
            "unabhaengige pruefung",
            "returncode",
            "stdout",
            "stderr",
        )
    ):
        _goal_required.add("exec")

    if any(
        token in _goal_text
        for token in (
            "lösche",
            "loesche",
            "delete",
            "cleanup",
            "aufräumen",
            "aufraeumen",
        )
    ):
        _goal_required.add("cleanup")

    def _goal_completed_families():
        completed = set()

        for _gc_item in tool_trace:
            if not isinstance(_gc_item, dict):
                continue

            if not self._tool_succeeded(
                _gc_item.get("result")
            ):
                continue

            _gc_name = str(
                _gc_item.get("name") or ""
            ).lower()

            _gc_args = (
                _gc_item.get("args")
                if isinstance(
                    _gc_item.get("args"),
                    dict,
                )
                else {}
            )

            if _gc_name in (
                "write_file",
                "patch_file",
                "create_file",
            ):
                completed.add("write")

            if _gc_name == "read_file":
                completed.add("read")

            if _gc_name in (
                "powershell",
                "shell",
                "run_script",
                "python_exec",
            ):
                completed.add("exec")

                _gc_command = str(
                    _gc_args.get("command")
                    or _gc_args.get("script")
                    or _gc_args.get("code")
                    or ""
                ).lower()

                if any(
                    token in _gc_command
                    for token in (
                        "remove-item",
                        " del ",
                        "erase ",
                        "unlink",
                        "os.remove",
                    )
                ):
                    completed.add("cleanup")

            if _gc_name in (
                "delete_file",
                "remove_file",
            ):
                completed.add("cleanup")

        return completed

    def _goal_missing_families():
        return sorted(
            _goal_required
            - _goal_completed_families()
        )

    last_model = None
    final_reply = ""
    last_error = None
    # NEXUS_MODEL_ERROR_AFTER_TOOLS_V1
    llm_failure = False
    _llm_code = None
    tool_trace: list[dict] = []
    progress_bits: list[str] = []

    # NEXUS_CHECKPOINT_RESTORE_V1
    # Wiederhergestellte Nachweise aus dem gespeicherten Zustand
    # bilden die Grundlage fuer Zielverifikation und Idempotenz.
    # Ungepruefte Legacy-Zustaende bleiben davon ausgeschlossen.
    if _restored_trace and _state_trusted:
        tool_trace.extend(_restored_trace)
    must_tool = bool(decision.get("force_tools")) or intent in (
        "build", "shell", "run_script", "execute",
    )
    finished = False

    # NEXUS_TASK_STATE_START_V1
    # NEXUS_RUN_ID_CHECKPOINT_V1
    from datetime import datetime as _dt
    from uuid import uuid4 as _uuid4

    _run_id = (
        "RUN-"
        + _dt.now().strftime("%Y%m%d%H%M%S")
        + "-"
        + _uuid4().hex[:6]
    )

    _goal_missing_now = (
        _goal_missing_families()
        if "_goal_completed_families" in locals()
        else []
    )

    self._write_task_state(
        status="ACTIVE",
        message=message,
        intent=intent,
        tools=self._tool_state_summary(
            _restored_trace if _state_trusted else []
        ),
        error=None,
        result=(
            "Resume aus gespeichertem Zustand"
            if _is_resume
            else "Auftrag gestartet"
        ),
        run_id=_run_id,
        checkpoint=(
            "resume" if _is_resume else "start"
        ),
        next_step=(
            ", ".join(_goal_missing_now)
            if _goal_missing_now
            else "erster Tool-Schritt"
        ),
        resumed=_is_resume,
    )

    self._act("tool", text="Starte echte Ausführung (Tools an)…", intent=intent)
    yield {"type": "progress", "text": "Starte echte Ausführung (Tools an)…\n"}

    for _round in range(6):
        llm, _llm_attempts = self._chat_with_retry(msgs)
        last_model = llm.get("model")
        if not llm.get("success"):
            _llm_code = str(
                llm.get("error") or "llm_failed"
            )
            llm_failure = True
            # NEXUS_MODEL_ERROR_AFTER_TOOLS_V1
            # Bereits ausgefuehrte Tools bleiben erhalten und werden
            # nicht wiederholt; der Zustand wird checkpointet.
            last_error = (
                "llm_failed_after_tools"
                if tool_trace
                else _llm_code
            )
            if not final_reply:
                if tool_trace:
                    final_reply = (
                        "Modellfehler nach bereits ausgefuehrten "
                        f"Tools ({_llm_code}, {_llm_attempts} "
                        "Versuch(e)). Tools wurden nicht erneut "
                        "ausgefuehrt; Zustand gespeichert."
                    )
                elif _llm_code == "ollama_down":
                    final_reply = (
                        "Ollama offline — Tools lokal nutzbar, "
                        "aber keine LLM-Fortsetzung."
                    )
                else:
                    final_reply = f"LLM-Fehler: {_llm_code}"
            break
        reply = llm.get("reply") or ""
        final_reply = reply
        calls = parse_tool_calls(reply)

        calls = filter_tool_calls(calls, message, technical_diag, _executed_signatures, _goal_required, _goal_completed_families)
        # NEXUS_GOAL_CONTRACT_CONTINUE_V1
        _goal_missing = _goal_missing_families()

        if (
            not calls
            and _goal_missing
            and _goal_contract_reprompts
            < _goal_contract_max_reprompts
        ):
            _goal_contract_reprompts += 1

            msgs.append({
                "role": "system",
                "content": (
                    "NEXUS GOAL CONTRACT: "
                    "Der Benutzerauftrag ist noch NICHT abgeschlossen. "
                    "Folgende reale Ausfuehrungsnachweise fehlen: "
                    + ", ".join(_goal_missing)
                    + ". Waehle jetzt ausschliesslich die naechsten "
                    "dafuer notwendigen Tools. "
                    "Keine Finalantwort und kein PASS, solange diese "
                    "Nachweise fehlen."
                ),
            })

            continue

        if (
            not calls
            and _goal_missing
            and _goal_contract_reprompts
            >= _goal_contract_max_reprompts
        ):
            last_error = "goal_contract_incomplete"

        if not calls:
            looks_dump = bool(_CODE_DUMP_RE.search(reply)) or (
                len(reply) > 350
                and "<tool_call>" not in reply
                and "```tool" not in reply.lower()
            )
            # Ohne bisherige Tools genau einmal passende Tool-Nutzung anfordern.
            # Explizite Benutzergrenzen haben Vorrang vor automatischer Tool-Auswahl.
            if must_tool and not tool_trace and _round == 0:
                nudge = (
                    "STOP. Tool-Ausfuehrung ist fuer diesen Auftrag erforderlich. "
                    "Waehle ausschliesslich das zur konkreten Benutzerabsicht passende Tool. "
                    "READ_ONLY, lesen, listen oder diagnostizieren darf niemals write_file ausloesen. "
                    "write_file nur bei ausdruecklicher Schreibabsicht UND vorhandenem Zielpfad UND Inhalt. "
                    "Keine Pfade, Dateinamen, Projekte oder Inhalte erfinden. "
                    "Explizite Benutzergrenzen wie KEIN write_file oder KEINE DATEI VERAENDERN haben Vorrang. "
                    "Fuehre genau den naechsten notwendigen Tool-Schritt aus."
                )
                msgs.append({"role": "assistant", "content": reply})
                msgs.append({"role": "user", "content": nudge})
                bit = "Tool-Ausführung entsprechend Benutzerabsicht erforderlich.\n\n"
                progress_bits.append(bit)
                self._act("tool", text="Passender Tool-Schritt erforderlich", intent=intent)
                yield {"type": "progress", "text": bit}
                continue
            finished = True
            break
        status_txt = reply
        for marker in ("<tool_call>", "```tool", "```json"):
            if marker in status_txt:
                status_txt = status_txt.split(marker, 1)[0]
        status_txt = status_txt.strip()
        names = ", ".join(str(c.get("name", "?")) for c in calls)
        if status_txt:
            bit = f"Als Nächstes: {status_txt[:280]}\n→ Tools: {names}\n\n"
        else:
            bit = f"Als Nächstes: Tools ausführen ({names})\n\n"
        progress_bits.append(bit)
        self._act("tool", text=bit.strip()[:200], intent=intent, tools=[str(c.get("name", "?")) for c in calls])
        yield {"type": "progress", "text": bit}

        msgs.append({"role": "assistant", "content": reply})

        # Explizit verlangte Post-Write-Verifikation ist ein echter Tool-Schritt,
        # kein Text-Claim. Wenn nach dem letzten Schreibvorgang noch kein
        # erfolgreicher read_file lief und der Agent zu Shell/PowerShell will,
        # wird der Read fuer exakt den zuletzt geschriebenen Pfad vorgeschaltet.
        low_message = (message or "").lower()
        verify_read_required = "read_file" in low_message and any(
            token in low_message for token in ("nach der reparatur", "erneut", "zweite", "zwingend", "verifiziere", "verify")
        )
        current_names = [str(c.get("name") or "").lower() for c in calls]
        # Fehlenden Post-Write-Read bereits IM AKTUELLEN BATCH einsetzen.
        # Beispiel: write_file -> read_file -> write_file -> powershell wird zu
        # write_file -> read_file -> write_file -> read_file -> powershell.
        # So wird der Shell-Aufruf nicht vor der verlangten Verifikation ausgefuehrt.
        if verify_read_required:
            shell_positions = [i for i, n in enumerate(current_names) if n in ("powershell", "shell", "run_script")]
            if shell_positions:
                first_shell = min(shell_positions)
                writes_before_shell = [i for i, n in enumerate(current_names[:first_shell]) if n in ("write_file", "patch_file", "create_file")]
                if writes_before_shell:
                    last_batch_write = max(writes_before_shell)
                    has_batch_read_after = any(
                        current_names[i] == "read_file"
                        for i in range(last_batch_write + 1, first_shell)
                    )
                    if not has_batch_read_after:
                        verify_path = (calls[last_batch_write].get("args") or {}).get("path")
                        if verify_path:
                            calls.insert(first_shell, {"name": "read_file", "args": {"path": verify_path}})
                            current_names.insert(first_shell, "read_file")
                            bit = "Als Nächstes: fehlende Post-Write-Verifikation im aktuellen Tool-Batch vor Shell/PowerShell eingefuegt\n\n"
                            progress_bits.append(bit)
                            yield {"type": "progress", "text": bit}
        # Zusaetzlich zum Wortlaut aus dem echten Toolzustand ableiten:
        # gab es bereits Write -> Read -> spaeteren Write und will der Agent
        # nun Shell/PowerShell ausfuehren, ist ein neuer Read nach dem letzten
        # Write zwingend, auch wenn das Modell den Prompt anders formuliert.
        prior_names_for_state = [str(x.get("name") or "").lower() for x in tool_trace]
        state_requires_postread = False
        prior_writes = [i for i, n in enumerate(prior_names_for_state) if n in ("write_file", "patch_file", "create_file")]
        if len(prior_writes) >= 2:
            last_write_for_state = max(prior_writes)
            earlier_write = min(prior_writes)
            had_read_between = any(n == "read_file" and earlier_write < i < last_write_for_state for i, n in enumerate(prior_names_for_state))
            has_read_after = any(n == "read_file" and i > last_write_for_state and self._tool_succeeded(tool_trace[i].get("result")) for i, n in enumerate(prior_names_for_state))
            state_requires_postread = had_read_between and not has_read_after
        verify_read_required = verify_read_required or state_requires_postread
        if verify_read_required and any(n in current_names for n in ("powershell", "shell", "run_script")):
            prior_names = [str(x.get("name") or "").lower() for x in tool_trace]
            write_positions = [i for i, n in enumerate(prior_names) if n in ("write_file", "patch_file", "create_file")]
            if write_positions:
                last_write = max(write_positions)
                post_reads = [i for i, n in enumerate(prior_names) if n == "read_file" and i > last_write and self._tool_succeeded(tool_trace[i].get("result"))]
                if not post_reads:
                    write_args = tool_trace[last_write].get("args") or {}
                    verify_path = write_args.get("path")
                    if verify_path:
                        # Nur den fehlenden Verifikationsschritt ausfuehren.
                        # Den bereits geplanten Batch hier NICHT anhaengen, sonst
                        # werden erfolgreiche Writes/Shell-Aufrufe erneut abgespult
                        # und der Agent laeuft bis ins Tool-Rundenlimit.
                        calls = [{"name": "read_file", "args": {"path": verify_path}}]
                        bit = "Als Nächstes: verpflichtende Post-Write-Verifikation mit read_file vor Shell/PowerShell\n\n"
                        progress_bits.append(bit)
                        yield {"type": "progress", "text": bit}
        try:
            # Gleichen read_file-Pfad pro Tool-Batch nur einmal ausfuehren.
            _seen_read_paths = set()
            _deduped_calls = []

            for _call in calls:
                if not isinstance(_call, dict):
                    continue

                _call_name = str(
                    _call.get("name") or ""
                ).lower()

                _call_args = _call.get("args") or {}

                if _call_name == "read_file":
                    _read_path = str(
                        _call_args.get("path") or ""
                    ).strip().lower()

                    if _read_path in _seen_read_paths:
                        continue

                    _seen_read_paths.add(_read_path)

                _deduped_calls.append(_call)

            calls = _deduped_calls

            executed = run_tools(calls, self)

            # NEXUS_RECORD_TOOL_SIGNATURES_V1
            #
            # Nur tatsaechlich an den Executor uebergebene Aufrufe
            # als erledigt markieren.
            for _executed_call in calls:
                if not isinstance(
                    _executed_call,
                    dict,
                ):
                    continue

                _executed_name = str(
                    _executed_call.get("name")
                    or ""
                ).lower()

                _executed_args = (
                    _executed_call.get("args")
                    if isinstance(
                        _executed_call.get("args"),
                        dict,
                    )
                    else {}
                )

                try:
                    import json as _json

                    _executed_key = (
                        _executed_name,
                        _json.dumps(
                            _executed_args,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                    )
                except Exception:
                    _executed_key = (
                        _executed_name,
                        repr(_executed_args),
                    )

                _executed_signatures.add(
                    _executed_key
                )

            # --------------------------------------------------
            # Missing-file recovery:
            #
            # read_file FAIL
            #   -> Parent-Verzeichnis bestimmen
            #   -> list_dir
            #   -> Ergebnis an naechste LLM-Runde geben
            #
            # Kein blindes Wiederholen desselben read_file.
            # --------------------------------------------------

            _existing_list_paths = {
                str((c.get("args") or {}).get("path") or "").strip().lower()
                for c in calls
                if isinstance(c, dict)
                and str(c.get("name") or "").lower() == "list_dir"
            }

            _recovery_paths = set()
            _recovery_calls = []

            for _item in executed:

                if not isinstance(_item, dict):
                    continue

                _tool_name = str(
                    _item.get("name") or ""
                ).lower()

                if _tool_name != "read_file":
                    continue

                _result = _item.get("result")

                if self._tool_succeeded(_result):
                    continue

                _args = _item.get("args") or {}
                _bad_path = str(
                    _args.get("path") or ""
                ).strip()

                if isinstance(_result, dict):
                    _error_text = str(
                        _result.get("error")
                        or _result.get("message")
                        or _result.get("reply")
                        or ""
                    )
                else:
                    _error_text = str(
                        _result or ""
                    )

                if not re.search(
                    r"(datei\s+nicht\s+gefunden|nicht\s+gefunden|not\s+found|no\s+such\s+file)",
                    _error_text,
                    re.I,
                ):
                    continue

                if not _bad_path:
                    _parent = "."
                else:
                    _clean_path = _bad_path.rstrip("\\/")

                    if re.search(r"[\\/]", _clean_path):
                        _parent = re.sub(
                            r"[\\/][^\\/]+$",
                            "",
                            _clean_path,
                        ) or "."
                    else:
                        _parent = "."

                _parent_key = _parent.lower()

                if (
                    _parent_key in _existing_list_paths
                    or _parent_key in _recovery_paths
                ):
                    continue

                _recovery_paths.add(_parent_key)

                _recovery_calls.append({
                    "name": "list_dir",
                    "args": {
                        "path": _parent
                    }
                })

            if _recovery_calls:
                _recovery_results = run_tools(
                    _recovery_calls,
                    self
                )

                executed.extend(
                    _recovery_results
                )

            # READ recovery:
            # Wenn read_file wegen eines nicht vorhandenen Pfads scheitert,
            # nicht denselben Pfad blind wiederholen. Stattdessen das
            # Elternverzeichnis listen und dieses reale Ergebnis der
            # naechsten Agent-Runde geben.
            recovery_calls = []

            for _item in executed:
                if not isinstance(_item, dict):
                    continue

                _name = str(_item.get("name") or "").lower()
                _result = _item.get("result")
                _args = _item.get("args") or {}

                if _name != "read_file" or self._tool_succeeded(_result):
                    continue

                _err = ""
                if isinstance(_result, dict):
                    _err = str(
                        _result.get("error")
                        or _result.get("message")
                        or _result.get("reply")
                        or ""
                    )
                else:
                    _err = str(_result or "")

                if not re.search(
                    r"(datei\s+nicht\s+gefunden|nicht\s+gefunden|not\s+found|no\s+such\s+file)",
                    _err,
                    re.I,
                ):
                    continue

                _bad_path = str(_args.get("path") or "").strip()

                if not _bad_path:
                    _parent = "."
                else:
                    _parent = re.sub(
                        r"[\\/][^\\/]+$",
                        "",
                        _bad_path.rstrip("\\/")
                    ) or "."

                # Gleichen list_dir-Recovery-Pfad nicht mehrfach
                # im selben Batch ausfuehren.
                if not any(
                    str(c.get("name") or "").lower() == "list_dir"
                    and str((c.get("args") or {}).get("path") or "") == _parent
                    for c in (calls + recovery_calls)
                ):
                    recovery_calls.append({
                        "name": "list_dir",
                        "args": {"path": _parent}
                    })

            if recovery_calls:
                recovery_executed = run_tools(recovery_calls, self)
                executed.extend(recovery_executed)

            # READ recovery:
            # Wenn read_file wegen eines nicht vorhandenen Pfads scheitert,
            # nicht denselben Pfad blind wiederholen. Stattdessen das
            # Elternverzeichnis listen und dieses reale Ergebnis der
            # naechsten Agent-Runde geben.
            recovery_calls = []

            for _item in executed:
                if not isinstance(_item, dict):
                    continue

                _name = str(_item.get("name") or "").lower()
                _result = _item.get("result")
                _args = _item.get("args") or {}

                if _name != "read_file" or self._tool_succeeded(_result):
                    continue

                _err = ""
                if isinstance(_result, dict):
                    _err = str(
                        _result.get("error")
                        or _result.get("message")
                        or _result.get("reply")
                        or ""
                    )
                else:
                    _err = str(_result or "")

                if not re.search(
                    r"(datei\s+nicht\s+gefunden|nicht\s+gefunden|not\s+found|no\s+such\s+file)",
                    _err,
                    re.I,
                ):
                    continue

                _bad_path = str(_args.get("path") or "").strip()

                if not _bad_path:
                    _parent = "."
                else:
                    _parent = re.sub(
                        r"[\\/][^\\/]+$",
                        "",
                        _bad_path.rstrip("\\/")
                    ) or "."

                # Gleichen list_dir-Recovery-Pfad nicht mehrfach
                # im selben Batch ausfuehren.
                if not any(
                    str(c.get("name") or "").lower() == "list_dir"
                    and str((c.get("args") or {}).get("path") or "") == _parent
                    for c in (calls + recovery_calls)
                ):
                    recovery_calls.append({
                        "name": "list_dir",
                        "args": {"path": _parent}
                    })

            if recovery_calls:
                recovery_executed = run_tools(recovery_calls, self)
                executed.extend(recovery_executed)
        except Exception:
            last_error = "tool_executor_exception"
            break
        if not isinstance(executed, list) or not executed:
            last_error = "tool_result_missing"
            break
        if any(not isinstance(item, dict) or not isinstance(item.get("name"), str) or "result" not in item for item in executed):
            last_error = "tool_result_missing"
            break
        tool_trace.extend(executed)

        # NEXUS_GOAL_CONTRACT_PROGRESS_V1
        # NEXUS_GOAL_ORDER_STATUS_V1
        _goal_missing_now = _goal_missing_families()

        _goal_done_now = _goal_completed_families()

        if _goal_missing_now:
            _next_goal_step = None

            for _candidate in (
                "write",
                "read",
                "exec",
                "cleanup",
            ):
                if (
                    _candidate in _goal_required
                    and _candidate not in _goal_done_now
                ):
                    _next_goal_step = _candidate
                    break

            if _next_goal_step:
                msgs.append({
                    "role": "system",
                    "content": (
                        "NEXUS EXECUTION ORDER: "
                        "Fuehre jetzt als naechsten Schritt "
                        + _next_goal_step.upper()
                        + " aus. Keine spaeteren Schritte vorziehen. "
                        "Cleanup ist immer der letzte Schritt."
                    ),
                })

        if _goal_missing_now:
            msgs.append({
                "role": "system",
                "content": (
                    "NEXUS GOAL CONTRACT STATUS: "
                    "Auftrag noch ACTIVE. Fehlende Schritte: "
                    + ", ".join(_goal_missing_now)
                    + ". Erfolgreiche Einzeltools bedeuten noch "
                    "keinen abgeschlossenen Gesamtauftrag."
                ),
            })

        # NEXUS_REPAIR_FAILURE_BUDGET_V1
        _retry_blocked = False
        _retry_block_reason = None

        for _retry_item in executed:
            if not isinstance(_retry_item, dict):
                continue

            _retry_result = _retry_item.get("result")

            if self._tool_succeeded(_retry_result):
                continue

            _retry_name = str(
                _retry_item.get("name") or ""
            ).lower()

            _retry_args = (
                _retry_item.get("args")
                if isinstance(
                    _retry_item.get("args"),
                    dict,
                )
                else {}
            )

            if isinstance(_retry_result, dict):
                _retry_error = str(
                    _retry_result.get("error")
                    or _retry_result.get("stderr")
                    or _retry_result.get("reply")
                    or ""
                )
            else:
                _retry_error = str(
                    _retry_result or ""
                )

            try:
                import json as _json

                _retry_arg_sig = _json.dumps(
                    _retry_args,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            except Exception:
                _retry_arg_sig = repr(
                    _retry_args
                )

            # Fehlertext begrenzen, damit variable Zeitstempel oder
            # lange stderr-Ausgaben die Signatur nicht unbrauchbar machen.
            _retry_error_sig = (
                _retry_error.strip()[:500]
            )

            _failure_key = (
                _retry_name,
                _retry_arg_sig,
                _retry_error_sig,
            )

            _repair_failure_counts[_failure_key] = (
                _repair_failure_counts.get(
                    _failure_key,
                    0,
                )
                + 1
            )

            _failure_count = (
                _repair_failure_counts[
                    _failure_key
                ]
            )

            if (
                _failure_count
                >= _max_repair_attempts
            ):
                _retry_blocked = True
                _retry_block_reason = (
                    "Wiederholte unveraenderte Fehlerursache: "
                    f"tool={_retry_name}, "
                    f"versuche={_failure_count}, "
                    f"fehler={_retry_error_sig}"
                )
                break

        if _retry_blocked:
            last_error = (
                "repair_retry_limit_reached"
            )

            self._write_task_state(
                status="BLOCKED",
                message=message,
                intent=intent,
                tools=[
                    {
                        "name": str(
                            x.get("name") or ""
                        ),
                        "ok": self._tool_succeeded(
                            x.get("result")
                        ),
                        "args": (
                            x.get("args")
                            if isinstance(
                                x.get("args"),
                                dict,
                            )
                            else {}
                        ),
                    }
                    for x in tool_trace[-50:]
                    if isinstance(x, dict)
                ],
                error=last_error,
                result=_retry_block_reason,
            )

            break

        # NEXUS_REPAIR_ORCHESTRATOR_V1
        #
        # Fehlgeschlagene Tool-Schritte werden klassifiziert.
        # Der Agent bekommt einen konkreten Recovery-Hinweis statt
        # denselben Aufruf blind zu wiederholen.
        _repair_hints = []

        for _repair_item in executed:
            if not isinstance(
                _repair_item,
                dict,
            ):
                continue

            _repair_name = str(
                _repair_item.get("name")
                or ""
            ).lower()

            _repair_result = (
                _repair_item.get("result")
            )

            if self._tool_succeeded(
                _repair_result
            ):
                continue

            _repair_args = (
                _repair_item.get("args")
                if isinstance(
                    _repair_item.get("args"),
                    dict,
                )
                else {}
            )

            if isinstance(
                _repair_result,
                dict,
            ):
                _repair_error = str(
                    _repair_result.get("error")
                    or _repair_result.get("stderr")
                    or _repair_result.get("reply")
                    or ""
                )
            else:
                _repair_error = str(
                    _repair_result or ""
                )

            _repair_error_low = (
                _repair_error.lower()
            )

            # ------------------------------------------
            # READ_FILE: Pfadproblem
            # ------------------------------------------
            if (
                _repair_name == "read_file"
                and any(
                    token in _repair_error_low
                    for token in (
                        "nicht gefunden",
                        "not found",
                        "no such file",
                        "kein verzeichnis",
                    )
                )
            ):
                _bad_path = str(
                    _repair_args.get("path")
                    or ""
                )

                _repair_hints.append(
                    "read_file fehlgeschlagen fuer "
                    f"{_bad_path!r}. "
                    "Nicht denselben Pfad erneut raten. "
                    "Nutze list_dir auf dem naechsten bekannten "
                    "Elternverzeichnis und waehle danach nur einen "
                    "tatsaechlich gelisteten Pfad."
                )
                continue

            # ------------------------------------------
            # WRITE_FILE: Permission / Schutz
            # ------------------------------------------
            if (
                _repair_name
                in (
                    "write_file",
                    "patch_file",
                    "create_file",
                )
                and any(
                    token in _repair_error_low
                    for token in (
                        "permission denied",
                        "zugriff verweigert",
                        "access denied",
                        "readonly",
                        "read-only",
                    )
                )
            ):
                _bad_path = str(
                    _repair_args.get("path")
                    or ""
                )

                _repair_hints.append(
                    "Schreibzugriff verweigert fuer "
                    f"{_bad_path!r}. "
                    "Schutz nicht eigenmaechtig umgehen. "
                    "Kein chmod/attrib/ACL-Fix ohne ausdruecklichen "
                    "Benutzerauftrag. Pruefe stattdessen, ob eine "
                    "andere fuer den Auftrag geeignete Projektdatei "
                    "geaendert werden kann. Wenn nicht: BLOCKED."
                )
                continue

            # ------------------------------------------
            # POWERSHELL/SHELL: echter Exitcode
            # ------------------------------------------
            if _repair_name in (
                "powershell",
                "shell",
                "run_script",
            ):
                _repair_hints.append(
                    f"{_repair_name} ist fehlgeschlagen. "
                    "Nutze stderr/returncode als Ursache. "
                    "Befehl nicht identisch wiederholen, ausser "
                    "eine konkrete Reparatur hat die Ursache zuvor "
                    "veraendert. Keine PASS-Meldung."
                )
                continue

            # ------------------------------------------
            # GENERISCHER FEHLER
            # ------------------------------------------
            _repair_hints.append(
                f"Tool {_repair_name!r} ist fehlgeschlagen. "
                "Nicht blind wiederholen. Ursache aus dem echten "
                "Tool-Ergebnis ableiten, dann genau einen sinnvollen "
                "Recovery-Schritt waehlen oder BLOCKED melden."
            )

        if _repair_hints:
            msgs.append({
                "role": "system",
                "content": (
                    "NEXUS RECOVERY-ANWEISUNG:\n"
                    + "\n".join(
                        "- " + h
                        for h in _repair_hints
                    )
                    + "\n\n"
                    "Der aktive Originalauftrag bleibt verbindlich. "
                    "Recovery dient nur diesem Auftrag."
                ),
            })

        # NEXUS_TASK_STATE_PROGRESS_V1
        # NEXUS_RUN_ID_CHECKPOINT_V1
        _task_tool_summary = self._tool_state_summary(
            tool_trace
        )

        _goal_missing_progress = (
            _goal_missing_families()
            if "_goal_missing_families" in locals()
            else []
        )

        self._write_task_state(
            status="ACTIVE",
            message=message,
            intent=intent,
            tools=_task_tool_summary,
            error=None,
            result="Tool-Fortschritt gespeichert",
            run_id=_run_id,
            checkpoint=f"round {_round}",
            next_step=(
                ", ".join(_goal_missing_progress)
                if _goal_missing_progress
                else "Finalantwort / Verifikation"
            ),
            resumed=_is_resume,
        )

        # NEXUS_AGENT_COMPLETION_GUARD_V1
        #
        # Wenn ein expliziter Arbeitsauftrag in diesem Batch bereits
        # erfolgreich ausgefuehrt wurde, darf der Agent danach keine
        # unaufgeforderten Zusatzaktionen erfinden.
        #
        # Besonders:
        # - kein status/system_monitor hinterher
        # - kein zweites write_file auf denselben Pfad
        # - kein "zur Sicherheit" veraenderter Inhalt
        _all_batch_ok = bool(executed) and all(
            isinstance(_x, dict)
            and self._tool_succeeded(_x.get("result"))
            for _x in executed
        )

        _batch_names = [
            str(_x.get("name") or "").lower()
            for _x in executed
            if isinstance(_x, dict)
        ]

        _real_work_names = {
            "read_file",
            "write_file",
            "patch_file",
            "create_file",
            "powershell",
            "shell",
            "run_script",
            "python_exec",
        }

        _did_real_work = any(
            _n in _real_work_names
            for _n in _batch_names
        )

        # NEXUS_AGENT_COMPLETION_GOAL_AWARE_V2
        #
        # Ein erfolgreicher Einzel-Tool-Batch darf einen mehrstufigen
        # Auftrag NICHT abschliessen, solange Goal-Contract-Schritte fehlen.
        _completion_goal_missing = (
            _goal_missing_families()
            if "_goal_missing_families" in locals()
            else []
        )

        _completion_is_multistep = bool(
            _completion_goal_missing
        )

        # Erfolgreicher expliziter Einzelauftrag:
        # Ergebnis direkt zur finalen Antwort geben.
        #
        # Der Agent darf nur dann weitere Tool-Runden starten,
        # wenn der Auftrag selbst mehrere noch offene Schritte verlangt.
        _request_low_completion = (message or "").lower()

        _explicit_single_action = any(
            token in _request_low_completion
            for token in (
                "führe diesen powershell",
                "fuehre diesen powershell",
                "lies die datei",
                "lese die datei",
                "öffne ",
                "oeffne ",
                "starte ",
            )
        )

        if (
            not _completion_is_multistep
            and _all_batch_ok
            and _did_real_work
            and _explicit_single_action
        ):
            # NEXUS_SINGLE_ACTION_GOAL_VERIFY_V1
            #
            # Ein erfolgreicher Einzel-Tool-Batch ist kein automatischer
            # Gesamterfolg: derselbe Zielnachweis wie im Hauptpfad muss
            # hier zwingend bestanden werden, sonst kein PASS.
            _single_goal_ok, _single_goal_error = (
                self._verify_goal_completion(
                    message=message,
                    intent=intent,
                    tool_trace=tool_trace,
                )
            )

            if not _single_goal_ok:
                yield {
                    "type": "done",
                    "reply": (
                        "".join(progress_bits)
                        + "BLOCKED: Ziel nicht durch reale "
                        "Ausfuehrung nachgewiesen "
                        f"({_single_goal_error or 'goal_not_proven'}). "
                        "Kein PASS gemeldet."
                    ),
                    "intent": intent,
                    "plan": steps,
                    "model": last_model,
                    "success": False,
                    "error": _single_goal_error or "goal_not_proven",
                    "data": {
                        "tools": tool_trace,
                        "execution_status": "INCOMPLETE",
                    },
                }
                return

            _last_item = executed[-1]
            _last_result = _last_item.get("result") or {}
            _last_name = str(
                _last_item.get("name") or ""
            )

            if _last_name in ("powershell", "shell"):
                _rc = _last_result.get("returncode")
                if _rc is None:
                    _rc = _last_result.get("exitcode")
                if _rc is None:
                    _rc = _last_result.get("exit_code")

                _reply = (
                    f"returncode: {_rc}\n"
                    f"stdout:\n{_last_result.get('stdout') or ''}\n"
                    f"stderr:\n{_last_result.get('stderr') or ''}"
                )

            elif _last_name == "read_file":
                _reply = (
                    "READ_FILE PASS\n"
                    f"path: {_last_result.get('path') or ''}\n"
                    f"bytes: {_last_result.get('bytes')}\n\n"
                    f"{_last_result.get('content') or ''}"
                )

            else:
                _reply = self._evidence_reply(
                    tool_trace,
                    model_unavailable=False,
                )

            yield {
                "type": "done",
                "reply": _reply,
                "intent": intent,
                "plan": steps,
                "model": last_model,
                "success": True,
                "error": None,
                "data": {
                    "tools": tool_trace,
                    "execution_status": "TOOLS_SUCCEEDED",
                },
            }
            return

        # NEXUS_EXPLICIT_SHELL_TRUTH_V1
        #
        # Ein explizit verlangter PowerShell-/Shell-Befehl wird nach
        # realer Ausfuehrung deterministisch abgeschlossen.
        #
        # Kein zweiter LLM-Durchlauf, kein status, keine erfundene
        # Zusammenfassung und keine Veraenderung von stdout/stderr.
        _low_original = (message or "").lower()

        _explicit_shell_request = (
            ("powershell" in _low_original or "shell" in _low_original)
            and any(
                token in _low_original
                for token in (
                    "führe",
                    "fuehre",
                    "ausführen",
                    "ausfuehren",
                    "befehl",
                    "command",
                )
            )
        )

        if _explicit_shell_request:
            _shell_items = [
                item
                for item in executed
                if isinstance(item, dict)
                and str(item.get("name") or "").lower()
                in ("powershell", "shell")
            ]

            if _shell_items:
                _shell_item = _shell_items[-1]
                _shell_result = _shell_item.get("result")

                if not isinstance(_shell_result, dict):
                    _shell_result = {
                        "success": False,
                        "error": "ungueltiges_executor_ergebnis",
                    }

                _rc = _shell_result.get("returncode")

                if _rc is None:
                    _rc = _shell_result.get("exitcode")

                if _rc is None:
                    _rc = _shell_result.get("exit_code")

                _stdout = _shell_result.get("stdout")
                _stderr = _shell_result.get("stderr")

                if _stdout is None:
                    _stdout = ""

                if _stderr is None:
                    _stderr = ""

                _shell_ok = self._tool_succeeded(
                    _shell_result
                )

                _shell_reply = (
                    f"returncode: {_rc}\n"
                    f"stdout:\n{_stdout}\n"
                    f"stderr:\n{_stderr}"
                )

                _shell_error = (
                    None
                    if _shell_ok
                    else (
                        _shell_result.get("error")
                        or "shell_failed"
                    )
                )

                _shell_event = {
                    "type": "done",
                    "reply": _shell_reply,
                    "intent": intent,
                    "plan": steps,
                    "model": last_model,
                    "success": _shell_ok,
                    "error": _shell_error,
                    "data": {
                        "tools": tool_trace,
                        "execution_status": (
                            "TOOLS_SUCCEEDED"
                            if _shell_ok
                            else "INCOMPLETE"
                        ),
                    },
                }

                self._act(
                    "done" if _shell_ok else "idle",
                    text=(
                        "PowerShell ausgefuehrt"
                        if _shell_ok
                        else f"PowerShell fehlgeschlagen: {_shell_error}"
                    ),
                    intent=intent,
                    ok=_shell_ok,
                    plan=steps,
                    tools=[
                        str(x.get("name"))
                        for x in tool_trace
                        if isinstance(x, dict)
                    ],
                )

                yield _shell_event
                return

        done_lines = []
        for item in executed:
            res = item.get("result")
            ok = self._tool_succeeded(res)
            res = res if isinstance(res, dict) else {}
            brief = res.get("reply") or res.get("error") or res.get("stdout") or ""
            if isinstance(brief, str) and len(brief) > 160:
                brief = brief[:160] + "…"
            done_lines.append(
                f"Gerade erledigt: {item.get('name')} → {'OK' if ok else 'FAIL'} {brief}".strip()
            )
            msgs.append({
                "role": "user",
                "content": format_tool_result(
                    item["name"],
                    item["result"],
                ),
            })

        # NEXUS_REASSERT_ACTIVE_GOAL_V1
        #
        # Nach jedem realen Tool-Batch den Originalauftrag erneut
        # in den aktiven Kontext stellen. Dadurch wird TOOL_RESULT
        # nicht versehentlich zum neuen Hauptauftrag.
        msgs.append({
            "role": "system",
            "content": (
                "AKTIVER AUFTRAG BLEIBT:\n"
                + _active_goal
                + "\n\n"
                "Bewerte die bisherigen echten Tool-Ergebnisse "
                "nur im Hinblick auf diesen Auftrag. "
                "Fuehre ausschliesslich noch offene notwendige "
                "Schritte aus. Keine Zusatzaufgaben erfinden."
            ),
        })
        done_bit = "\n".join(done_lines) + "\n\n"
        progress_bits.append(done_bit)
        yield {"type": "progress", "text": done_bit}

    v = validate_reply(final_reply or "Keine Antwort.", intent=intent)
    clean = v["reply"]
    for marker in ("<tool_call>", "```tool"):
        if marker in clean:
            head = clean.split(marker, 1)[0].strip()
            if head:
                clean = head
    prefix = "".join(progress_bits)
    if prefix and not clean.startswith(prefix[: min(40, len(prefix))]):
        combined = (prefix + "Ergebnis:\n" + clean).strip()
    else:
        combined = clean
    if not last_error:
        # Truth-Guard: eine explizit verlangte Post-Write-read_file-Pruefung
        # muss im echten Trace nach dem letzten Write vorhanden sein.
        low_message = (message or "").lower()
        verify_read_required = "read_file" in low_message and any(
            token in low_message for token in ("nach der reparatur", "erneut", "zweite", "zwingend", "verifiziere", "verify")
        )
        if verify_read_required and tool_trace:
            trace_names = [str(x.get("name") or "").lower() for x in tool_trace]
            writes = [i for i, n in enumerate(trace_names) if n in ("write_file", "patch_file", "create_file")]
            if writes:
                last_write = max(writes)
                verified_reads = [i for i, n in enumerate(trace_names) if n == "read_file" and i > last_write and self._tool_succeeded(tool_trace[i].get("result"))]
                if not verified_reads:
                    last_error = "required_post_write_read_missing"
        if not last_error and must_tool and not tool_trace:
            last_error = "no_tool_execution"
        elif not last_error:
            _unrecovered_failures = []

            for _index, _item in enumerate(tool_trace):

                if not isinstance(_item, dict):
                    continue

                _result = _item.get("result")

                if self._tool_succeeded(_result):
                    continue

                _tool_name = str(
                    _item.get("name") or ""
                ).lower()

                if isinstance(_result, dict):
                    _error_text = str(
                        _result.get("error")
                        or _result.get("message")
                        or _result.get("reply")
                        or ""
                    )
                else:
                    _error_text = str(
                        _result or ""
                    )

                _missing_read = (
                    _tool_name == "read_file"
                    and bool(re.search(
                        r"(datei\s+nicht\s+gefunden|nicht\s+gefunden|not\s+found|no\s+such\s+file)",
                        _error_text,
                        re.I,
                    ))
                )

                _recovered = False

                if _missing_read:
                    for _later in tool_trace[_index + 1:]:

                        if not isinstance(_later, dict):
                            continue

                        if (
                            str(_later.get("name") or "").lower()
                            == "list_dir"
                            and self._tool_succeeded(
                                _later.get("result")
                            )
                        ):
                            _recovered = True
                            break

                if not _recovered:
                    _unrecovered_failures.append(
                        _item
                    )

            if _unrecovered_failures:
                last_error = "tool_failed_or_unconfirmed"
        elif not finished:
            last_error = "tool_round_limit"
    # NEXUS_COMPLETION_TRUTH_GUARD_V1
    #
    # Ein erfolgreicher Einzel-Tool-Aufruf ist nicht automatisch ein
    # erfolgreich abgeschlossener Gesamtauftrag.
    if not last_error and tool_trace:
        _trace_names = [
            str(x.get("name") or "").lower()
            for x in tool_trace
            if isinstance(x, dict)
        ]

        _failed_items = [
            x for x in tool_trace
            if isinstance(x, dict)
            and not self._tool_succeeded(
                x.get("result")
            )
        ]

        if _failed_items:
            last_error = (
                "tool_failed_or_unconfirmed"
            )

        _write_positions = [
            i for i, n in enumerate(_trace_names)
            if n in (
                "write_file",
                "patch_file",
                "create_file",
            )
        ]

        if (
            not last_error
            and _write_positions
        ):
            _last_write = max(
                _write_positions
            )

            _verified_after_write = any(
                i > _last_write
                and n == "read_file"
                and self._tool_succeeded(
                    tool_trace[i].get("result")
                )
                for i, n in enumerate(
                    _trace_names
                )
            )

            _request_low_truth = (
                message or ""
            ).lower()

            _verification_required = any(
                token in _request_low_truth
                for token in (
                    "verifiziere",
                    "verify",
                    "prüfe",
                    "pruefe",
                    "kontrolliere",
                    "lies danach",
                    "read_file",
                    "zwingend",
                    "exakt",
                )
            )

            if (
                _verification_required
                and not _verified_after_write
            ):
                last_error = (
                    "required_post_write_read_missing"
                )

    # NEXUS_GOAL_VERIFY_BEFORE_PASS_V1
    # NEXUS_MODEL_ERROR_AFTER_TOOLS_V1
    #
    # Ein Modellfehler darf den realen Zielstand weder zerstoeren
    # noch verdecken: berechtigte PASS-Nachweise bleiben erhalten,
    # und fehlende Nachweise werden weiterhin benannt.
    _llm_goal_error = None

    if last_error is None or llm_failure:
        (
            _goal_ok,
            _goal_error,
        ) = self._verify_goal_completion(
            message=message,
            intent=intent,
            tool_trace=tool_trace,
        )

        if llm_failure:
            if _goal_ok and tool_trace:
                last_error = None
                final_reply = (
                    self._evidence_reply(tool_trace)
                    or final_reply
                )
            elif tool_trace:
                _llm_goal_error = _goal_error
                last_error = "llm_failed_after_tools"
        elif not _goal_ok:
            last_error = (
                _goal_error
                or "goal_not_proven"
            )

    success_flag = last_error is None

    # NEXUS_TASK_STATE_FINAL_V1
    # NEXUS_RUN_ID_CHECKPOINT_V1
    _final_tool_summary = self._tool_state_summary(
        tool_trace
    )

    # Teilweise ausgefuehrte Auftraege duerfen nicht als BLOCKED
    # verloren gehen: PARTIAL ist der ehrliche Zustand fuer bereits
    # real ausgefuehrte Schritte mit offenem Restziel.
    if success_flag:
        _final_status = "PASS"
    elif tool_trace:
        _final_status = "PARTIAL"
    else:
        _final_status = "BLOCKED"

    _final_missing = (
        _goal_missing_families()
        if "_goal_missing_families" in locals()
        else []
    )

    self._write_task_state(
        status=_final_status,
        message=message,
        intent=intent,
        tools=_final_tool_summary,
        error=last_error,
        result=(
            final_reply
            or "Keine Finalantwort."
        ),
        run_id=_run_id,
        checkpoint="final",
        next_step=(
            ", ".join(_final_missing)
            if _final_missing
            else None
        ),
        resumed=_is_resume,
    )

    if last_error:
        explanations = {
            "no_tool_execution": "Kein gueltiger Werkzeugaufruf erkannt. Es wurde nichts ausgefuehrt; der Auftrag bleibt offen.",
            "tool_failed_or_unconfirmed": "Mindestens ein Werkzeug ist fehlgeschlagen oder hat keinen eindeutigen Erfolgsstatus geliefert.",
            "tool_round_limit": "Tool-Rundenlimit erreicht. Der Auftrag ist noch nicht abgeschlossen.",
            "tool_result_missing": "Der Executor hat kein Werkzeugergebnis geliefert.",
            "tool_executor_exception": "Der Executor hat eine Ausnahme gemeldet. Ausfuehrungsstand unbekannt; nicht blind wiederholen.",
            "required_post_write_read_missing": "Die verlangte read_file-Verifikation nach dem letzten Schreibvorgang wurde nicht real ausgefuehrt. Ein PASS-Claim dafuer ist ungueltig.",
            # NEXUS_REPAIR_RETRY_EXPLANATION_V1
            "repair_retry_limit_reached": "Dieselbe Fehlerursache ist wiederholt unveraendert aufgetreten. Weitere blinde Wiederholungen wurden gestoppt.",

            # NEXUS_GOAL_VERIFY_ERRORS_V1
            "goal_read_not_proven": "Der verlangte Datei-Lesevorgang ist nicht durch einen erfolgreichen read_file-Aufruf belegt.",
            "goal_write_not_proven": "Die verlangte Dateiaenderung ist nicht durch einen erfolgreichen Schreib- oder Patch-Aufruf belegt.",
            "goal_write_verify_not_proven": "Die verlangte Verifikation nach der Dateiaenderung fehlt.",
            "goal_execution_not_proven": "Die verlangte reale Ausfuehrung ist nicht durch einen erfolgreichen Executor-Aufruf belegt.",
            "goal_repair_not_proven": "Es wurde keine reale Reparatur oder ausfuehrende Aenderung nachgewiesen.",
            "goal_status_not_proven": "Der verlangte Systemstatus wurde nicht durch einen passenden realen Tool-Aufruf ermittelt.",
            "goal_no_real_tool_evidence": "Der Auftrag benoetigt reale Ausfuehrung, aber es existiert kein erfolgreicher Tool-Nachweis.",
            "goal_not_proven": "Das eigentliche Benutzerziel konnte nicht durch reale Ausfuehrungsnachweise bestaetigt werden.",

            # NEXUS_GOAL_CONTRACT_EXPLANATION_V1
            "goal_contract_incomplete": "Der Zielvertrag ist nicht vollstaendig erfuellt: es fehlen real ausgefuehrte Nachweise. Kein PASS.",

            # NEXUS_MODEL_RETRY_EXPLANATIONS_V1
            "llm_failed": "Der Modellaufruf ist fehlgeschlagen. Ein Abschluss wurde nicht nachgewiesen.",
            "llm_failed_after_tools": (
                "Der Modellaufruf ist nach bereits ausgefuehrten Tools "
                "fehlgeschlagen. Die ausgefuehrten Tools wurden NICHT "
                "wiederholt, der Zustand ist gespeichert. Fortsetzung "
                "mit 'weiter'."
            ),
            "ollama_down": (
                "Ollama ist nicht erreichbar. Ein Abschluss wurde nicht "
                "nachgewiesen; der Zustand ist gespeichert."
            ),
            "no_model": (
                "Ollama laeuft, aber es ist kein Modell gefunden worden. "
                "Ein Abschluss wurde nicht nachgewiesen."
            ),
            "tool_round_limit_reached": "Das Tool-Rundenlimit wurde erreicht. Der Auftrag ist noch nicht abgeschlossen.",
        }
        _reason = explanations.get(last_error)
        if _reason is None:
            _reason = (
                f"Fehlercode '{str(last_error)[:200]}': "
                "der Auftrag wurde nicht abgeschlossen."
            )
        if last_error == "llm_failed_after_tools":
            if _llm_code:
                _reason += f" Modellfehler: {str(_llm_code)[:200]}."
            if _llm_goal_error:
                _reason += (
                    " Offene Nachweise: "
                    f"{str(_llm_goal_error)[:200]}."
                )
        combined = prefix + "BLOCKED: " + _reason
    # A health score is not evidence that this assignment was completed.
    out_ev = {
        "type": "done", "reply": combined, "intent": intent, "plan": steps,
        "model": last_model, "success": success_flag, "error": last_error,
        "data": {"tools": tool_trace, "execution_status": (
            "TOOLS_SUCCEEDED" if success_flag and tool_trace else
            "NOT_EXECUTED" if not tool_trace and last_error == "no_tool_execution" else
            "INCOMPLETE" if last_error else "NO_TOOLS_REQUIRED"
        )},
    }
    self._act(
        "done" if out_ev.get("success") else "idle",
        text=("Fertig" if out_ev.get("success") else f"Fehler: {last_error}"),
        intent=intent,
        ok=bool(out_ev.get("success")),
        plan=steps,
        tools=[str(x.get("name")) for x in tool_trace] if tool_trace else None,
    )
    yield out_ev
