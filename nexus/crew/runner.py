"""CrewService — Spezialagenten mit gefilterten Tools über Master-LLM."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexus.common.paths import WORKSPACE, ensure_dirs
from nexus.crew.roster import get_agent, list_public_agents, public_view
from nexus.crew.capabilities import (
    ORCHESTRATOR_ID,
    filter_tool_calls,
    normalize_agent_key,
    orchestrator_role_error,
    resolve_role_alias,
)
from nexus.master.tools import format_tool_result, looks_like_tool_attempt, parse_tool_calls, run_tools
from nexus.master.validator import validate_reply

ACTIVE_FILE = WORKSPACE / "crew" / "active.json"
MAX_ROUNDS = 6


class CrewService:
    """Liste, Active-Persistenz und Agent-Lauf (wie Master-Agent, mit Persona + Tool-Filter)."""

    def __init__(self) -> None:
        ensure_dirs()
        (WORKSPACE / "crew").mkdir(parents=True, exist_ok=True)

    def list_agents(self) -> dict:
        return {"success": True, "agents": list_public_agents()}

    def get(self, id_or_tag: str) -> dict:
        agent = get_agent(id_or_tag)
        if not agent:
            return {"success": False, "error": "not_found", "reply": f"Agent unbekannt: {id_or_tag}"}
        return {"success": True, "agent": public_view(agent)}

    def get_active(self) -> dict:
        aid = self._load_active()
        agent = get_agent(aid) if aid else None
        return {
            "success": True,
            "id": aid,
            "agent": public_view(agent) if agent else None,
        }

    def set_active(self, id_or_tag: str | None) -> dict:
        raw = (id_or_tag or "").strip()
        if not raw or raw.lower() in ("none", "null", "clear", "-", "off"):
            self._save_active("")
            # leere active.json / id
            try:
                if ACTIVE_FILE.is_file():
                    ACTIVE_FILE.write_text("{\"id\": null}\n", encoding="utf-8")
            except Exception:
                pass
            return {
                "success": True,
                "id": None,
                "agent": None,
                "reply": "Kein Agent mehr aktiv.",
            }
        agent = get_agent(raw)
        if not agent:
            return {"success": False, "error": "not_found", "reply": f"Agent unbekannt: {raw}"}
        self._save_active(agent["id"])
        return {
            "success": True,
            "id": agent["id"],
            "agent": public_view(agent),
            "reply": f"Aktiv: @{agent['tag']} {agent['emoji']}",
        }

    def run(
        self,
        master: Any,
        agent_id: str,
        message: str,
        session_messages: list | None = None,
    ) -> dict:
        """Agent-Loop: System-Prompt + capability allowlist, max 6 rounds. Controlled errors to orchestrator."""
        session_messages = session_messages or []

        # Phase 4: WEB/VOICE/UI have no crew agent — controlled handoff (no fake agent)
        _mapped, role_key = resolve_role_alias(normalize_agent_key(agent_id))
        if role_key is not None and _mapped is None:
            return orchestrator_role_error(role_key)

        agent = get_agent(agent_id)
        if not agent:
            # Fallback: active agent
            aid = self._load_active()
            agent = get_agent(aid) if aid else None
        if not agent:
            return {
                "success": False,
                "ok": False,
                "status": "error",
                "reply": "Kein Crew-Agent gewaehlt. Nutze @Coder/@Scout/@Fixer/@Media/@Ops/@Lens/@Rap oder Rollen CODE/FILES/TERMINAL/SYSTEM.",
                "intent": "crew",
                "agent": None,
                "error": "no_agent",
                "orchestrator": ORCHESTRATOR_ID,
            }

        self._save_active(agent["id"])
        allowed = set(agent.get("tools") or [])
        # Phase 4 capability allowlist is source of truth
        from nexus.crew.capabilities import allowed_tools_for as _allowed_tools_for
        _cap_allowed = _allowed_tools_for(agent["id"])
        if _cap_allowed:
            allowed = _cap_allowed
        shell_seen: dict[str, int] = {}
        tools_csv = ", ".join(sorted(allowed)) if allowed else "(keine)"
        progress_rule = (
            " PFLICHT: Vor jedem tool_call kurz auf Deutsch schreiben was du als Nächstes tust. "
            "Nach jedem TOOL_RESULT zuerst: (1) Gerade erledigt: … (2) Als Nächstes: … "
            "Dann weiter Tools oder Finalantwort. Keine Simulation. "
            "NIEMALS Befehlsausgaben erfinden."
        )
        tool_hint = (
            f"Du antwortest als @{agent['tag']} {agent['emoji']}. "
            f"Erlaubte Tools NUR: {tools_csv}. "
            'Format: <tool_call>{"name":"…","args":{…}}</tool_call> oder ```tool mit JSON. '
            "Andere Tools nicht aufrufen. Finalantwort ohne Tool wenn fertig."
            + progress_rule
        )
        persona = (agent.get("system_prompt") or "").strip()
        extra = f"{persona}\n\n{tool_hint}".strip()
        # t363u: klare Auftraege direkt liefern (echte Websuche / echte Logfehler)
        from nexus.crew import direct as _direct
        try:
            _pre = _direct.prepare(agent, message or "", master)
        except Exception:
            _pre = None
        if _pre and _pre.get("final"):
            return {"success": True, "ok": True, "status": "ok",
                    "reply": _pre["final"],
                    "intent": "crew", "agent": public_view(agent), "model": None, "error": None,
                    "ollama": True, "orchestrator": ORCHESTRATOR_ID,
                    "data": {"tools": _pre.get("tools") or [], "execution_status": "TOOLS_SUCCEEDED",
                             "provenance": "live_tool_result"}}
        if _pre and _pre.get("context"):
            extra += _pre["context"]

        user_message = (message or "").strip() or "Hallo."
        requested_tools: set[str] = set()
        low_message = user_message.lower()
        if "powershell" in low_message:
            requested_tools.update({"powershell", "shell"})
        if any(x in low_message for x in ("datei", "file", "ordner", "directory")):
            requested_tools.update({"write_file", "read_file", "list_dir", "patch_file", "create_file"})
        strict_scope = bool(requested_tools)
        if strict_scope:
            extra += (
                "\nAUFTRAGSTREUE: Nutze nur Werkzeuge, die fuer den konkreten Nutzerauftrag notwendig sind. "
                "Keine zusaetzlichen Health-, Monitor-, Status- oder Diagnose-Tools ohne ausdrueckliche Anforderung. "
                "Befehle und Argumente vollstaendig und unverkuerzt in tool_call ausgeben."
            )
        msgs = master.build_context(session_messages, extra=extra)
        msgs.append({"role": "user", "content": user_message})

        last_model = None
        final_reply = ""
        last_error = None
        tool_trace: list[dict] = []
        progress_bits: list[str] = []
        parse_retry_used = False

        progress_bits.append(f"✦ @{agent['tag']} übernimmt…\n")
        if _pre and _pre.get("errors"):
            progress_bits.append("Gelesen (Logs): " + " | ".join(_pre["errors"][-3:])[:600] + "\n\n")

        for _round in range(MAX_ROUNDS):
            llm = master.llm.chat(msgs)
            last_model = llm.get("model")
            if not llm.get("success"):
                last_error = llm.get("error")
                if not final_reply:
                    final_reply = llm.get("reply") or (
                        "Ollama offline — Crew wartet auf LLM."
                        if last_error == "ollama_down"
                        else f"LLM-Fehler: {last_error}"
                    )
                break
            reply = llm.get("reply") or ""
            final_reply = reply
            calls = parse_tool_calls(reply, shell_fences=True)
            # Tool-Filter: capability allowlist only
            calls, denied = filter_tool_calls(agent["id"], calls)
            if strict_scope:
                scoped_calls = []
                for call in calls:
                    name = str(call.get("name") or "").lower()
                    if name in requested_tools:
                        scoped_calls.append(call)
                    else:
                        denied.append({"name": name, "reason": "not_requested_by_user"})
                calls = scoped_calls


            if denied and not calls:
                deny_msg = (
                    f"TOOL_RESULT\n{json.dumps({'tool': 'deny', 'denied': denied, 'allowed': sorted(allowed)}, ensure_ascii=False)}\n"
                    "Diese Tools sind für dich gesperrt. Nutze nur erlaubte oder Finalantwort."
                )
                msgs.append({"role": "assistant", "content": reply})
                msgs.append({"role": "user", "content": deny_msg})
                bit = f"Als Nächstes: unerlaubte Tools blockiert ({', '.join(str(d) for d in denied)})\n\n"
                progress_bits.append(bit)
                continue

            if not calls:
                # 2026-10-29: falsches Werkzeugformat nicht als NO_TOOLS_REQUIRED verbuchen
                if looks_like_tool_attempt(reply):
                    if not parse_retry_used:
                        parse_retry_used = True
                        msgs.append({"role": "assistant", "content": reply})
                        msgs.append({"role": "user", "content": (
                            "TOOL_PARSE_FAILED: Dein Werkzeugaufruf hatte kein gueltiges Format und wurde NICHT ausgefuehrt. "
                            'Gib ihn exakt so aus: <tool_call>{"name":"<tool>","args":{...}}</tool_call> '
                            "mit einem erlaubten Tool (" + tools_csv + ") - oder antworte final ohne Tool.")})
                        progress_bits.append("Als Naechstes: Werkzeugformat ungueltig - Korrektur angefordert\n\n")
                        continue
                    last_error = "TOOL_PARSE_FAILED"
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

            msgs.append({"role": "assistant", "content": reply})
            # Explizite Verifikationspflicht aus dem Nutzerauftrag nicht stillschweigend
            # ueberspringen: nach einem Schreibschritt muss bei gefordertem read_file
            # mindestens ein spaeterer read_file-Nachweis moeglich bleiben.
            executed_names_before = [str(x.get("name") or "").lower() for x in tool_trace]
            current_names = [str(x.get("name") or "").lower() for x in calls]
            verify_read_required = "read_file" in requested_tools and any(
                token in low_message for token in ("erneut", "danach", "wieder ein", "verifiziere", "verify")
            )
            if verify_read_required and any(n in current_names for n in ("write_file", "patch_file", "create_file")):
                extra += "\nNach diesem Schreibschritt ist ein anschliessender read_file-Nachweis weiterhin Pflicht."

            # Harte Ablaufgarantie: Will der Agent nach einem bereits ausgefuehrten
            # Schreibschritt direkt zu Shell/PowerShell weiter, obwohl der Nutzer
            # eine erneute read_file-Verifikation verlangt hat, fuehren wir den
            # fehlenden Read fuer exakt den zuletzt geschriebenen Pfad zuerst aus.
            if verify_read_required and any(n in current_names for n in ("powershell", "shell", "run_script")):
                prior_names = [str(x.get("name") or "").lower() for x in tool_trace]
                write_positions = [i for i, n in enumerate(prior_names) if n in ("write_file", "patch_file", "create_file")]
                if write_positions:
                    last_write = max(write_positions)
                    post_reads = [i for i, n in enumerate(prior_names) if n == "read_file" and i > last_write]
                    if not post_reads:
                        write_args = tool_trace[last_write].get("args") or {}
                        verify_path = write_args.get("path")
                        if verify_path:
                            calls = [{"name": "read_file", "args": {"path": verify_path}}] + calls
                            current_names = ["read_file"] + current_names
                            progress_bits.append("Als Nächstes: verpflichtende Post-Write-Verifikation mit read_file vor Shell/PowerShell\n\n")
            executed = run_tools(calls, master)
            tool_trace.extend(executed)
            # Controlled: break identical elevated shell loops (no uncontrolled shell loops)
            loop_hit = False
            for item in executed:
                n = (item.get("name") or "").lower()
                if n in ("powershell", "shell", "run_script"):
                    args = item.get("args") or {}
                    key = n + "|" + str(args.get("command") or args.get("path") or args)[:240]
                    shell_seen[key] = shell_seen.get(key, 0) + 1
                    if shell_seen[key] >= 2:
                        loop_hit = True
            if loop_hit:
                last_error = "shell_loop_guard"
                final_reply = (
                    "CONTROLLED_STOP: identischer Shell/Script-Aufruf wiederholt — "
                    "Abbruch an Orchestrator " + ORCHESTRATOR_ID + " (kein unkontrollierter Loop)."
                )
                progress_bits.append("Gerade erledigt: shell_loop_guard STOP\n\n")
                break
            done_lines = []
            for item in executed:
                res = item.get("result") or {}
                ok = res.get("success", res.get("ok", True))
                brief = res.get("reply") or res.get("error") or res.get("stdout") or ""
                if isinstance(brief, str) and len(brief) > 160:
                    brief = brief[:160] + "…"
                done_lines.append(
                    f"Gerade erledigt: {item.get('name')} → {'OK' if ok else 'FAIL'} {brief}".strip()
                )
                msgs.append({
                    "role": "user",
                    "content": format_tool_result(item["name"], item["result"]),
                })
            progress_bits.append("\n".join(done_lines) + "\n\n")

        v = validate_reply(final_reply or "Keine Antwort.", intent="crew")
        clean = v["reply"]
        for marker in ("<tool_call>", "```tool"):
            if marker in clean:
                head = clean.split(marker, 1)[0].strip()
                if head:
                    clean = head
        prefix = "".join(progress_bits)
        # Eddie: nur das Ergebnis zeigen (Fortschritt bleibt in data/progress)
        combined = _direct.result_only(clean)

        def _result_ok(item: dict) -> bool:
            res = item.get("result") if isinstance(item, dict) else None
            if not isinstance(res, dict) or res.get("error") or res.get("blocked"):
                return False
            flags = [res[k] for k in ("success", "ok") if k in res]
            if any(v is not True for v in flags):
                return False
            if "returncode" in res and (type(res["returncode"]) is not int or res["returncode"] != 0):
                return False
            return bool(flags) or (type(res.get("returncode")) is int and res["returncode"] == 0)

        # Explizite Post-Write-Verifikation muss tatsaechlich nach dem letzten
        # Schreibschritt stattgefunden haben; ein frueherer read_file reicht nicht.
        verify_read_required = "read_file" in requested_tools and any(
            token in low_message for token in ("erneut", "danach", "wieder ein", "verifiziere", "verify")
        )
        if verify_read_required and tool_trace:
            names = [str(x.get("name") or "").lower() for x in tool_trace]
            write_positions = [i for i, n in enumerate(names) if n in ("write_file", "patch_file", "create_file")]
            read_positions = [i for i, n in enumerate(names) if n == "read_file" and _result_ok(tool_trace[i])]
            if write_positions:
                last_write = max(write_positions)
                post_write_verified = any(i > last_write for i in read_positions)
                if not post_write_verified:
                    last_error = "required_post_write_read_missing"

        expected_exit_codes: set[int] = set()
        import re as _re
        for m in _re.finditer(r"(?:exitcode|exit\s*code|exit)\s*[:=]?\s*(\d+)", low_message):
            try:
                expected_exit_codes.add(int(m.group(1)))
            except Exception:
                pass

        def _result_matches_expected_failure(item: dict) -> bool:
            if not expected_exit_codes or not isinstance(item, dict):
                return False
            name = str(item.get("name") or "").lower()
            if name not in ("powershell", "shell", "run_script"):
                return False
            res = item.get("result") or {}
            code = res.get("returncode", res.get("exit_code"))
            if type(code) is not int or code not in expected_exit_codes:
                return False
            args = item.get("args") or {}
            command = str(args.get("command") or args)
            return (f"exit {code}" in command.lower()) or (f"exitcode {code}" in low_message) or (f"exitcode: {code}" in low_message)

        tools_ok = all(_result_ok(item) or _result_matches_expected_failure(item) for item in tool_trace) if tool_trace else True
        ok_flag = last_error is None and tools_ok
        if tool_trace and not tools_ok and last_error is None:
            last_error = "tool_failed_or_unconfirmed"
            ok_flag = False
        out: dict[str, Any] = {
            "success": ok_flag,
            "ok": ok_flag,
            "status": "ok" if ok_flag else "error",
            "reply": combined,
            "intent": "crew",
            "agent": public_view(agent),
            "model": last_model,
            "error": last_error,
            "ollama": last_error is None,
            "orchestrator": ORCHESTRATOR_ID,
        }
        if tool_trace:
            out["data"] = {"tools": tool_trace, "execution_status": "TOOLS_SUCCEEDED" if ok_flag else "INCOMPLETE"}
        elif last_error is None:
            out["data"] = {"tools": [], "execution_status": "NO_TOOLS_REQUIRED"}
        else:
            out["data"] = {"tools": [], "execution_status": "TOOL_PARSE_FAILED" if last_error == "TOOL_PARSE_FAILED" else "INCOMPLETE"}
        return out

    def _load_active(self) -> str | None:
        if not ACTIVE_FILE.exists():
            return None
        try:
            data = json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))
            return data.get("id")
        except Exception:
            return None

    def _save_active(self, agent_id: str) -> None:
        ACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACTIVE_FILE.write_text(
            json.dumps({"id": agent_id}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
