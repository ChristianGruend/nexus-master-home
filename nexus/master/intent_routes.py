"""Frühe Intents außerhalb des LLM-Agent-Loops."""
from __future__ import annotations

import re

from nexus.common.paths import MASTER_PROMPT
from nexus.master.tools import format_tool_result


def route_early(self, message: str, session_messages: list, decision: dict, intent: str, steps: list, run_tools):
    if decision.get("early"):
        if intent == "capabilities":
            from nexus.master.evidence import capability_snapshot, capability_text
            evidence = capability_snapshot()
            return self._ok(capability_text(evidence), intent, steps, data=evidence)
        if intent == "ui":
            action = decision.get("action", "")
            reply = decision.get("reply") or f"OK: {action}"
            ui_data = {"action": action, "ui": True}
            if decision.get("url"):
                ui_data["url"] = decision.get("url")
            if action == "close_pages":
                osr = self.os_actions.close_opened(decision.get("url") or None)
                ui_data["os"] = osr
                if not decision.get("reply") and osr.get("reply"):
                    reply = osr["reply"]
            return self._ok(reply, intent, steps, data=ui_data)
        if intent == "diagnose" and not re.search(
            r"\b(tool[- ]?router|router|controller|decision|validator|quellcode|code|datei|funktion|read[_ -]?only)\b",
            (message or "").lower(),
            re.I,
        ):
            mon = self.monitor
            data = mon.collect(write_history=False)
            o = data.get("ollama") or {}
            alerts = data.get("alerts") or []
            alert_bits = []
            for a in alerts[:5]:
                if isinstance(a, dict):
                    alert_bits.append(str(a.get("message_de") or a.get("message") or a))
                else:
                    alert_bits.append(str(a))
            alert_txt = "; ".join(alert_bits) or "keine"
            cpu = ((data.get("cpu") or {}).get("percent"))
            ram = ((data.get("ram") or {}).get("percent"))
            oll = "an" if o.get("reachable") else "aus"
            pref = str(o.get("preferred") or "")
            lines = [
                "Diagnose (echt, kein Hilfe-Text):",
                "• API/Health: online",
                f"• Ollama: {oll}" + (f" · {pref}" if pref and o.get("reachable") else ""),
                f"• CPU {cpu}% · RAM {ram}%",
                "• Shell/Tools: aktiv · Monitor: aktiv",
                f"• Alerts: {alert_txt}",
                "",
                "Echte Befehle: powershell: … | wie hoch ist meine cpu | zeig leistung | sprich wieder / nur schreiben.",
                "Konkreten Bau nennen (baue X), dann Tools.",
            ]
            reply = "\n".join(lines)
            return self._ok(reply, intent, steps, data=data)
        if intent == "monitor":
            q = decision.get("query") or message
            mon = self.monitor
            reply = mon.answer_query(q)
            data = mon.collect(write_history=False)
            return self._ok(reply, intent, steps, data=data)
        if intent == "status":
            mon = self.monitor
            data = mon.collect(write_history=False)
            o = data.get("ollama") or {}
            model = o.get("preferred")
            reply = (
                f"NEXUS online. Ollama: {'an' if o.get('reachable') else 'aus'}"
                f"{(' · ' + model) if model else ''}. "
                f"System: {mon.summary_de(data)}"
            )
            return self._ok(reply, intent, steps, data=data)
        if intent == "media":
            action = decision.get("action", "play")
            # Browser-Playlist + bei play ohne Tracks → OS-Player versuchen
            fn = getattr(self.media, action, self.media.play)
            r = fn()
            if action == "play":
                st = (r.get("state") or {})
                if not st.get("count"):
                    osr = self.os_actions.play_media(None)
                    if osr.get("success"):
                        reply = osr.get("reply") or "Musik-App gestartet."
                        return self._ok(reply, "os_play_media", steps, data={**r, "os": osr})
            reply = decision.get("reply") or r.get("reply", action)
            return self._ok(reply, intent, steps, data=r)
        if intent == "os_open_url":
            r = self.os_actions.open_url(decision.get("url") or "")
            return self._ok(r.get("reply") or ("OK" if r.get("success") else "Fehler"), intent, steps, data=r)
        if intent == "os_open_path":
            r = self.os_actions.open_path(
                decision.get("path") or "",
                reveal=bool(decision.get("reveal")),
            )
            return self._ok(r.get("reply") or ("OK" if r.get("success") else "Fehler"), intent, steps, data=r)
        if intent == "os_volume":
            r = self.os_actions.volume(decision.get("action") or "up", decision.get("steps") or 4)
            return self._ok(r.get("reply") or ("OK" if r.get("success") else "Fehler"), intent, steps, data=r)
        if intent == "os_mouse":
            return self._mouse_move_via_bridge(decision, steps)
        if intent == "torch":
            reply = decision.get("reply") or "BLOCKED: Taschenlampe nur auf Mobil."
            return {
                "success": False,
                "reply": reply,
                "intent": intent,
                "plan": steps,
                "model": None,
                "error": decision.get("error") or "torch_pc_na",
                "data": {"blocked": True},
            }
        if intent == "os_launch_app":
            r = self.os_actions.launch_app(decision.get("name") or "")
            return self._ok(r.get("reply") or ("OK" if r.get("success") else "Fehler"), intent, steps, data=r)
        if intent == "os_play_media":
            r = self.os_actions.play_media(decision.get("path"))
            return self._ok(r.get("reply") or ("OK" if r.get("success") else "Fehler"), intent, steps, data=r)
        if intent in ("os_close_opened", "os_close_url"):
            r = self.os_actions.close_opened(decision.get("url") or None)
            return self._ok(r.get("reply") or ("OK" if r.get("success") else "Fehler"), intent, steps, data=r)
        if intent == "project_list_dir":
            path = decision.get("path")
            if not isinstance(path, str) or not path:
                return {"success": False, "reply": "BLOCKED: Kein eindeutiger Ordnerpfad.",
                        "intent": intent, "plan": steps, "error": "missing_path"}
            call = {"name": "list_dir", "args": {"path": path}}
            try:
                trace = run_tools([call], self)
            except Exception:
                return {"success": False, "reply": "BLOCKED: list_dir-Executor hat eine Ausnahme gemeldet.",
                        "intent": intent, "plan": steps, "error": "tool_executor_exception",
                        "data": {"tools": [], "execution_status": "INCOMPLETE"}}
            if (not isinstance(trace, list) or len(trace) != 1 or
                    not isinstance(trace[0], dict) or "result" not in trace[0]):
                return {"success": False, "reply": "BLOCKED: Kein list_dir-Ergebnis geliefert.",
                        "intent": intent, "plan": steps, "error": "tool_result_missing",
                        "data": {"tools": [], "execution_status": "INCOMPLETE"}}
            result = trace[0]["result"]
            ok = self._tool_succeeded(result)
            reply = format_tool_result("list_dir", result)
            return {"success": ok, "reply": reply, "intent": intent, "plan": steps,
                    "model": None, "error": None if ok else "tool_failed_or_unconfirmed",
                    "data": {"tools": trace, "execution_status": "TOOLS_SUCCEEDED" if ok else "INCOMPLETE"}}
        if intent == "os_list_dir":
            r = self.os_actions.list_dir_os(decision.get("path") or "")
            names = ", ".join(i["name"] for i in (r.get("items") or [])[:40]) or "(leer)"
            reply = r.get("reply") or f"Inhalt: {names}"
            return self._ok(reply, intent, steps, data=r)
        if intent == "correction_store":
            r = self.corrections.add(decision.get("text", ""), raw=decision.get("text", ""))
            return self._ok(r.get("reply", "ok"), intent, steps, data=r)
        if intent == "self_improve":
            # NEXUS_SELF_IMPROVE_TRUTH_V1
            #
            # Self-Improve darf keine Verbesserung behaupten, wenn bei
            # apply_safe keine reale Aenderung nachgewiesen ist.
            #
            # Der zentrale Master-Prompt darf ausserdem nur veraendert
            # werden, wenn Eddy ihn ausdruecklich im Auftrag nennt.

            _apply_safe = bool(
                decision.get("apply_safe")
            )

            _request_low_si = (
                message or ""
            ).lower()

            _master_explicit = any(
                token in _request_low_si
                for token in (
                    "master_prompt_runtime.txt",
                    "master prompt",
                    "master-prompt",
                )
            )

            _master_before = None

            try:
                if (
                    MASTER_PROMPT.exists()
                    and not _master_explicit
                ):
                    _master_before = (
                        MASTER_PROMPT.read_bytes()
                    )
            except Exception:
                _master_before = None

            try:
                r = self.self_improve.run(
                    trigger=str(
                        decision.get("trigger")
                        or "chat"
                    ),
                    apply_safe=_apply_safe,
                )
            except Exception as exc:
                return {
                    "success": False,
                    "reply": (
                        "BLOCKED: Self-Improve-Executorfehler: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "intent": intent,
                    "plan": steps,
                    "model": None,
                    "error": "self_improve_exception",
                    "data": {
                        "execution_status": "INCOMPLETE"
                    },
                }

            # --------------------------------------------------
            # Master-Prompt gegen ungefragte Veraenderung schuetzen
            # --------------------------------------------------

            _master_restored = False
            _master_guard_error = None

            if (
                _master_before is not None
                and not _master_explicit
            ):
                try:
                    _master_after = (
                        MASTER_PROMPT.read_bytes()
                        if MASTER_PROMPT.exists()
                        else None
                    )

                    if _master_after != _master_before:
                        MASTER_PROMPT.write_bytes(
                            _master_before
                        )
                        _master_restored = True

                except Exception as exc:
                    _master_guard_error = (
                        f"{type(exc).__name__}: {exc}"
                    )

            if _master_guard_error:
                return {
                    "success": False,
                    "reply": (
                        "BLOCKED: Self-Improve hat den geschuetzten "
                        "Master-Prompt beruehrt und der Rueckweg "
                        "konnte nicht sicher bestaetigt werden. "
                        f"Fehler: {_master_guard_error}"
                    ),
                    "intent": intent,
                    "plan": steps,
                    "model": None,
                    "error": "master_prompt_guard_failed",
                    "data": r,
                }

            # --------------------------------------------------
            # Reale Aenderungsnachweise sammeln
            # --------------------------------------------------

            _change_evidence = []

            if isinstance(r, dict):
                for _key in (
                    "changed_files",
                    "modified_files",
                    "written_files",
                    "files_changed",
                    "applied_changes",
                    "changes",
                ):
                    _value = r.get(_key)

                    if isinstance(_value, list):
                        _change_evidence.extend(
                            str(x)
                            for x in _value
                            if x
                        )

                    elif _value:
                        _change_evidence.append(
                            f"{_key}={_value}"
                        )

                _tool_items = (
                    r.get("tools")
                    or r.get("tool_trace")
                    or []
                )

                if isinstance(_tool_items, list):
                    for _item in _tool_items:
                        if not isinstance(_item, dict):
                            continue

                        _tool_name = str(
                            _item.get("name") or ""
                        ).lower()

                        _tool_result = (
                            _item.get("result")
                        )

                        if (
                            _tool_name in (
                                "write_file",
                                "patch_file",
                                "create_file",
                                "powershell",
                                "shell",
                                "python_exec",
                            )
                            and self._tool_succeeded(
                                _tool_result
                            )
                        ):
                            _change_evidence.append(
                                f"tool:{_tool_name}"
                            )

            if _master_restored:
                _change_evidence.append(
                    "master_prompt_restored"
                )

            _base_ok = self._tool_succeeded(r)

            # Analyse ohne apply_safe darf erfolgreich sein,
            # auch wenn keine Datei geaendert wurde.
            #
            # apply_safe bedeutet dagegen:
            # Keine belegte Aenderung = kein Verbesserungs-PASS.
            if (
                _apply_safe
                and not _change_evidence
            ):
                return {
                    "success": False,
                    "reply": (
                        "BLOCKED: Self-Improve meldete keine "
                        "belegte angewendete Aenderung. "
                        "Keine Fake-Verbesserung wird als PASS gewertet."
                    ),
                    "intent": intent,
                    "plan": steps,
                    "model": None,
                    "error": "self_improve_no_change_evidence",
                    "data": r,
                }

            _ok = bool(_base_ok)

            if not _ok:
                return {
                    "success": False,
                    "reply": (
                        r.get("reply")
                        if isinstance(r, dict)
                        else None
                    ) or (
                        "BLOCKED: Self-Improve ohne "
                        "gueltigen Erfolgsnachweis."
                    ),
                    "intent": intent,
                    "plan": steps,
                    "model": None,
                    "error": (
                        r.get("error")
                        if isinstance(r, dict)
                        else None
                    ) or "self_improve_unconfirmed",
                    "data": r,
                }

            _reply_lines = [
                (
                    r.get("reply")
                    if isinstance(r, dict)
                    else None
                ) or "Self-Improve abgeschlossen."
            ]

            if _apply_safe:
                _reply_lines.append(
                    "Aenderungsnachweis: "
                    + ", ".join(
                        _change_evidence[:20]
                    )
                )

            if _master_restored:
                _reply_lines.append(
                    "WARNUNG: Ungefragte Aenderung am "
                    "Master-Prompt wurde automatisch rueckgaengig gemacht."
                )

            return {
                "success": True,
                "reply": "\n".join(_reply_lines),
                "intent": intent,
                "plan": steps,
                "model": None,
                "error": None,
                "data": r,
            }
        if intent == "memory_store":
            r = self.memory.merk(decision.get("text", ""))
            return self._ok(r.get("reply", "ok"), intent, steps, data=r)
        if intent == "memory_recall":
            r = self.memory.recall(decision.get("query", ""))
            return self._ok(r.get("reply", ""), intent, steps, data=r)
        if intent == "download":
            r = self.downloads.enqueue(decision.get("url", ""))
            return self._ok(r.get("reply", ""), intent, steps, data=r)
        if intent == "project_open":
            r = self.projects.open_by_name(decision.get("name", ""))
            return self._ok(r.get("reply", ""), intent, steps, data=r)
        # Echte Shell: early action run
        if intent == "shell" and decision.get("action") == "run" and decision.get("command"):
            from nexus.common.config import Config
            to = decision.get("timeout")
            if to is None and decision.get("timeout_hint"):
                to = Config.SHELL_TIMEOUT_BUILD
            r = self.shell.run(
                decision["command"],
                shell=decision.get("shell") or "auto",
                timeout=to,
                build_mode=bool(decision.get("timeout_hint")),
            )
            # Executor evidence is authoritative. Preserve returncode/stdout/stderr
            # in the response and never convert a failed execution into success.
            reply = (
                f"returncode: {r.get('returncode')}\n"
                f"stdout:\n{r.get('stdout') or ''}\n"
                f"stderr:\n{r.get('stderr') or ''}"
            )
            ok = self._tool_succeeded(r)
            return {
                "success": ok,
                "reply": reply,
                "intent": intent,
                "plan": steps,
                "model": None,
                "error": None if ok else (r.get("error") or "shell_failed"),
                "data": r,
            }
        if intent == "run_script" and decision.get("action") == "run" and decision.get("path"):
            from nexus.common.config import Config
            to = decision.get("timeout") or Config.SHELL_TIMEOUT_BUILD
            r = self.shell.run_script(
                decision["path"],
                args=decision.get("args"),
                timeout=to,
                cwd=decision.get("cwd"),
            )
            reply = (
                f"returncode: {r.get('returncode')}\n"
                f"stdout:\n{r.get('stdout') or ''}\n"
                f"stderr:\n{r.get('stderr') or ''}"
            )
            ok = self._tool_succeeded(r)
            return {
                "success": ok,
                "reply": reply,
                "intent": intent,
                "plan": steps,
                "model": None,
                "error": None if ok else (r.get("error") or "script_failed"),
                "data": r,
            }
        if decision.get("reply"):
            return self._ok(decision["reply"], intent, steps)


    if intent in ("youtube_search", "youtube_play"):
        q = decision.get("query") or message
        open_browser = bool(decision.get("open_browser"))
        play_first = bool(decision.get("play_first")) or intent == "youtube_play"
        r = self.youtube.search(
            q,
            open_browser=open_browser,
            play_first=play_first,
            os_actions=self.os_actions,
        )
        if r.get("blocked") or not r.get("success"):
            reply = r.get("reply") or (
                "BLOCKED: YouTube-Suche fehlgeschlagen (" + str(r.get("error")) + ")."
            )
            return {
                "success": False,
                "reply": reply,
                "intent": intent,
                "plan": steps,
                "model": None,
                "error": r.get("error") or "youtube_search_blocked",
                "data": r,
            }
        return self._ok(r.get("reply", ""), intent, steps, data=r)

    if intent == "research":
        q = decision.get("query") or message
        r = self.research.search(q)
        if r.get("blocked") or not r.get("success"):
            reply = r.get("reply") or (
                r.get("reply") or ("SEARCH_FAIL: Web-Suche fehlgeschlagen (" + str(r.get("error")) + ").")
            )
            return {
                "success": False,
                "reply": reply,
                "intent": intent,
                "plan": steps,
                "model": None,
                "error": r.get("error") or "web_search_blocked",
                "data": r,
            }
        summary = r.get("reply", "")
        # Optional kurze DE-Zusammenfassung — NUR aus Treffern, nichts erfinden
        ping = self.llm.ping()
        if ping.get("reachable") and r.get("results"):
            ground = []
            for i, item in enumerate(r.get("results") or [], 1):
                title = item.get("title") or ""
                snip = item.get("snippet") or "(kein Snippet)"
                url = item.get("url") or ""
                ground.append(f"{i}. {title}\n   Snippet: {snip}\n   URL: {url}")
            extra = (
                "Du fasst NUR die folgenden Live-Web-Treffer kurz auf Deutsch zusammen. "
                "Keine erfundenen News, kein Knowledge-Cutoff-Theater, keine Fakten ausserhalb der Treffer. "
                "Jede Aussage an Titel/Snippet/URL binden. Am Ende die URLs listen."
            )
            msgs = self.build_context(session_messages, extra=extra)
            treffer = "\n".join(ground)
            msgs.append({
                "role": "user",
                "content": (
                    f"Query: {q}\n\nTREFFER:\n{treffer}"
                    "\n\nKurze grounded Zusammenfassung (nur aus Treffern):"
                ),
            })
            llm = self.llm.chat(msgs)
            if llm.get("success") and (llm.get("reply") or "").strip():
                url_lines = []
                for item in (r.get("results") or [])[:6]:
                    url_lines.append(f"- {item.get('title', '')}: {item.get('url', '')}")
                urls = "\n".join(url_lines)
                summary = (llm["reply"].strip() + "\n\nQuellen:\n" + urls).strip()
                return self._ok(summary, intent, steps, data={**r, "model": llm.get("model")})
        return self._ok(summary, intent, steps, data=r)

    if intent == "code" and not self._needs_agent(intent, decision, message):
        r = self.code.run(message)
        if r.get("success"):
            reply = f"Code ausgeführt.\nstdout:\n{r.get('stdout') or '(leer)'}"
        else:
            reply = f"Code-Fehler: {r.get('error') or r.get('stderr')}"
        return self._ok(reply, intent, steps, data=r)

    if intent == "task":
        t = self.tasks.create(message[:120], plan=steps, status="Active")
        reply = f"Task angelegt ({t['task']['id']}): {t['task']['title']}\nSchritte: " + ", ".join(s["detail"] for s in steps)
        llm = self.llm.chat(self.build_context(session_messages) + [{"role": "user", "content": message}])
        if llm.get("success"):
            reply += "\n\n" + llm["reply"]
            self.tasks.update(t["task"]["id"], result=llm["reply"][:2000])
        elif llm.get("error") == "ollama_down":
            reply += "\n\n(Hinweis: Ollama offline — Task gespeichert, Ausführung später.)"
        return self._ok(reply, intent, steps, data=t)

    if intent == "file":
        low = message.lower()
        if "liste" in low or "zeige" in low:
            r = self.files.list()
            names = ", ".join(i["name"] for i in r.get("items", [])) or "(leer)"
            return self._ok(f"Dateien in workspace/files: {names}", intent, steps, data=r)


    if intent == "crew":
        agent_id = decision.get("agent_id") or ""
        query = decision.get("query") or message
        return self.crew.run(self, agent_id, query, session_messages)

    return None
