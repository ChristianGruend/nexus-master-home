"""Tool-Parsing und echte Ausführung für den Master-Agent-Loop."""
from __future__ import annotations

import json
import re
from typing import Any

from nexus.common.config import Config
from nexus.computer.shell import _clamp_timeout

# ```tool / ```json tool fences und <tool_call>...</tool_call>
_FENCE_RE = re.compile(
    r"```(?:tool|json|tool_call)?\s*\n(\s*\{[\s\S]*?\})\s*\n```",
    re.I,
)
_XML_RE = re.compile(
    r"<tool[_-]call>\s*(\{[\s\S]*?\})\s*</tool[_-]call>",
    re.I,
)
_TOOL_OBJ_RE = re.compile(
    r'\{\s*"(?:name|tool)"\s*:\s*"[^"]+"\s*,\s*"(?:args|arguments|parameters)"\s*:\s*\{[\s\S]*?\}\s*\}',
    re.I,
)

KNOWN_TOOLS = {
    "powershell",
    "shell",
    "run_script",
    "python_exec",
    "read_file",
    "write_file",
    "list_dir",
    "status",
    "system_monitor",
    "open_url",
    "open_path",
    "launch_app",
    "play_media",
    "list_dir_os",
    "close_opened",
    "close_url",
    "web_search",
    "youtube_search",
    "youtube_play",
    "volume",
    "github",
}

# Build detection: keep New-ShaderProject word-match if user says shader;
# default scaffold name/examples elsewhere → Setup-AI-Agent.
_BUILD_HINT = re.compile(
    r"\b(npm\s+(create|install|i|run)|npx\s+|vite|create-vite|pnpm\s+|yarn\s+|"
    r"New-ShaderProject|Setup-AI-Agent|scaffold|create-project)\b",
    re.I,
)


def _normalize_call(obj: dict) -> dict | None:
    if not isinstance(obj, dict):
        return None
    name = (obj.get("name") or obj.get("tool") or "").strip().lower()
    if name == "ps":
        name = "powershell"
    if name in ("script", "run-script", "exec_script"):
        name = "run_script"
    if name in ("open_file", "open_folder", "reveal"):
        name = "open_path"
    if name in ("open_app", "start_app", "run_app", "starte_app"):
        name = "launch_app"
    if name in ("play_music", "play_audio", "play_file", "spiele_musik"):
        name = "play_media"
    if name in ("open_website", "open_web", "browse"):
        name = "open_url"
    if name in ("close_url", "close_pages", "close_browser_tabs", "schliess_seiten"):
        name = "close_opened"
    if name in ("research", "search_web", "duckduckgo", "websearch", "duck_duck_go_search", "duckduckgo_search", "ddg_search", "ddg"):
        name = "web_search"
    if name in ("yt_search", "youtube", "search_youtube", "suche_youtube"):
        name = "youtube_search"
    if name in ("gh", "git", "github_cli"):
        name = "github"
    if name not in KNOWN_TOOLS:
        return None
    args = obj.get("args") or obj.get("arguments") or obj.get("parameters") or {}
    if not isinstance(args, dict):
        args = {}
    # Flatten common aliases
    if name in ("powershell", "shell") and "command" not in args:
        for k in ("cmd", "code", "script"):
            if k in args:
                args["command"] = args[k]
                break
    if name == "run_script":
        if "path" not in args:
            for k in ("script", "file", "rel"):
                if k in args:
                    args["path"] = args[k]
                    break
    if name == "python_exec" and "code" not in args:
        for k in ("source", "script", "python"):
            if k in args:
                args["code"] = args[k]
                break
    if name in ("read_file", "write_file", "list_dir") and "path" not in args:
        for k in ("file", "rel", "dir"):
            if k in args:
                args["path"] = args[k]
                break
    if name == "open_url" and "url" not in args:
        for k in ("link", "href", "target", "u"):
            if k in args:
                args["url"] = args[k]
                break
    if name == "close_opened" and "url" not in args:
        for k in ("link", "href", "target", "u"):
            if k in args:
                args["url"] = args[k]
                break
    if name in ("open_path", "play_media", "list_dir_os") and "path" not in args:
        for k in ("file", "dir", "target", "filepath"):
            if k in args:
                args["path"] = args[k]
                break
    if name in ("web_search", "youtube_search") and "query" not in args:
        for k in ("q", "text", "topic", "suche"):
            if k in args:
                args["query"] = args[k]
                break
    if name == "launch_app" and "name" not in args:
        for k in ("app", "application", "program", "exe"):
            if k in args:
                args["name"] = args[k]
                break
    return {"name": name, "args": args}


# 2026-10-29: Modelle schreiben Werkzeugaufrufe oft als ```powershell/```shell/```ps1-Block.
_SHELL_FENCE_RE = re.compile(r"```(powershell|pwsh|ps1|ps|shell|bash|cmd|sh)[ \t]*\r?\n([\s\S]*?)\r?\n?```", re.I)
_TOOL_ATTEMPT_RE = re.compile(
    r"```(?:tool|tool_call|powershell|pwsh|ps1|shell|bash|cmd)\b|<tool[_-]call>|\"(?:name|tool)\"\s*:\s*\"\w+\""
    r"|^\s*`?(?:" + "|".join(sorted(KNOWN_TOOLS)) + r")`?(?:\s*\(|\s+\w+\s*=)",
    re.I | re.M,
)


def looks_like_tool_attempt(text: str) -> bool:
    """True wenn die Antwort erkennbar einen Werkzeugaufruf versucht (auch in falschem Format)."""
    return bool(_TOOL_ATTEMPT_RE.search(text or ""))


def parse_shell_fences(text: str) -> list[dict]:
    out: list[dict] = []
    for m in _SHELL_FENCE_RE.finditer(text or ""):
        lang = m.group(1).lower()
        body = m.group(2).strip()
        if not body or body.startswith("{"):
            continue
        name = "shell" if lang in ("shell", "bash", "cmd", "sh") else "powershell"
        out.append({"name": name, "args": {"command": body}})
    return out


def parse_tool_calls(text: str, *, shell_fences: bool = False) -> list[dict]:
    """Extrahiert Tool-Aufrufe aus Fences und XML-Tags. Reihenfolge beibehalten."""
    text = text or ""
    found: list[dict] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        raw = raw.strip()
        if not raw or raw in seen:
            return
        try:
            obj = json.loads(raw)
        except Exception:
            return
        # Einzelobjekt oder Liste
        items = obj if isinstance(obj, list) else [obj]
        for it in items:
            call = _normalize_call(it if isinstance(it, dict) else {})
            if call:
                key = json.dumps(call, sort_keys=True, ensure_ascii=False)
                if key not in seen:
                    seen.add(key)
                    found.append(call)
        seen.add(raw)

    for m in _XML_RE.finditer(text):
        _add(m.group(1))
    for m in _FENCE_RE.finditer(text):
        _add(m.group(1))
    # Fallback: lose JSON-Objekte mit name+args
    for m in _TOOL_OBJ_RE.finditer(text):
        _add(m.group(0))

    # Robuster Fallback: JSON-Objekte per Decoder aus Fliesstext lesen.
    # Damit funktionieren auch verschachtelte args, zusaetzlicher Text und
    # Provider-Formate wie {"tool_call": {...}} ohne Fake-Ausfuehrung.
    decoder = json.JSONDecoder()
    for pos, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text[pos:])
        except Exception:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("tool_call"), dict):
            obj = obj["tool_call"]
        if isinstance(obj, dict):
            call = _normalize_call(obj)
            if call:
                key = json.dumps(call, sort_keys=True, ensure_ascii=False)
                if key not in seen:
                    seen.add(key)
                    found.append(call)

    if shell_fences and not found:
        found.extend(parse_shell_fences(text))
    return found


def format_tool_result(name: str, result: dict) -> str:
    """Kompakte TOOL_RESULT-Nachricht für den LLM-Kontext."""
    payload = {"tool": name, "result": result}
    try:
        body = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        body = str(payload)
    if len(body) > 14000:
        body = body[:14000] + "…"
    return f"TOOL_RESULT\n{body}"


def _timeout_from_args(args: dict, *, build_hint: bool = False) -> int:
    raw = args.get("timeout")
    default = Config.SHELL_TIMEOUT_BUILD if build_hint else Config.SHELL_TIMEOUT
    return _clamp_timeout(raw, default=default)


def run_one(call: dict, master: Any) -> dict:
    """Einen Tool-Call wirklich ausführen."""
    name = (call.get("name") or "").lower()
    args = call.get("args") or {}
    try:
        if name in ("powershell", "shell"):
            cmd = args.get("command") or ""
            sh = args.get("shell") or ("powershell" if name == "powershell" else "auto")
            if name == "powershell" and _has_unbalanced_powershell_quotes(str(cmd)):
                return {
                    "success": False,
                    "error": "powershell_malformed_command",
                    "message": "PowerShell command has unbalanced quotes; command was not executed.",
                }
            cwd = args.get("cwd")
            build_mode = bool(args.get("build") or args.get("build_mode"))
            if not build_mode and _BUILD_HINT.search(str(cmd)):
                build_mode = True
            timeout = _timeout_from_args(args, build_hint=build_mode)
            return master.shell.run(
                cmd, shell=sh, timeout=timeout, cwd=cwd, build_mode=build_mode
            )

        if name == "run_script":
            path = args.get("path") or args.get("script") or ""
            script_args = args.get("args") or args.get("arguments") or args.get("params")
            cwd = args.get("cwd")
            timeout = _timeout_from_args(args, build_hint=True)
            return master.shell.run_script(path, args=script_args, timeout=timeout, cwd=cwd)

        if name == "python_exec":
            return master.code.run(args.get("code") or "")

        if name == "read_file":
            return master.project_fs.read(args.get("path") or "")

        if name == "write_file":
            return master.project_fs.write(args.get("path") or "", args.get("content") or "")

        if name == "list_dir":
            return master.project_fs.list(args.get("path") or "")

        if name == "status":
            return master.system.status()

        if name == "system_monitor":
            mon = getattr(master, "monitor", None)
            if mon is None:
                from nexus.computer.monitor import get_monitor
                mon = get_monitor()
            q = args.get("query") or args.get("q") or ""
            if q:
                reply = mon.answer_query(str(q))
                data = mon.collect(write_history=False)
                return {"success": True, "reply": reply, "data": data}
            data = mon.collect()
            return {"success": True, "reply": mon.summary_de(data), "data": data}

        osact = getattr(master, "os_actions", None)
        if osact is None:
            from nexus.computer.os_actions import get_os_actions
            osact = get_os_actions()

        if name == "open_url":
            return osact.open_url(args.get("url") or "")

        if name == "open_path":
            return osact.open_path(
                args.get("path") or "",
                reveal=bool(args.get("reveal") or args.get("folder")),
            )

        if name == "launch_app":
            return osact.launch_app(args.get("name") or args.get("app") or "")

        if name == "play_media":
            return osact.play_media(args.get("path") or None)

        if name == "list_dir_os":
            return osact.list_dir_os(args.get("path") or "")

        if name == "close_opened":
            return osact.close_opened(args.get("url") or None)

        if name == "web_search":
            research = getattr(master, "research", None)
            if research is None:
                from nexus.knowledge.research import Research
                research = Research()
            return research.search(str(args.get("query") or args.get("q") or ""))

        if name in ("youtube_search", "youtube_play"):
            yt = getattr(master, "youtube", None)
            if yt is None:
                from nexus.knowledge.youtube import YouTubeSearch
                yt = YouTubeSearch()
            play_first = bool(args.get("play_first") or args.get("play") or name == "youtube_play")
            return yt.search(
                str(args.get("query") or args.get("q") or ""),
                open_browser=bool(args.get("open_browser") or args.get("open")),
                play_first=play_first,
                os_actions=osact,
            )

        if name == "github":
            from nexus.integrations import github_tool
            return github_tool.run(str(args.get("command") or args.get("cmd") or ""), agent="NEXUS",
                                   cwd=args.get("cwd"), approval_id=args.get("approval_id"))

        if name == "volume":
            return osact.volume(str(args.get("action") or "up"), args.get("steps") or 4)

        return {"success": False, "error": f"unbekanntes Tool: {name}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _has_unbalanced_powershell_quotes(command: str) -> bool:
    """Conservative guard for clearly truncated LLM-generated PowerShell strings."""
    single = False
    double = False
    i = 0
    while i < len(command):
        ch = command[i]
        if ch == "`":
            i += 2
            continue
        if ch == "'" and not double:
            if single and i + 1 < len(command) and command[i + 1] == "'":
                i += 2
                continue
            single = not single
        elif ch == '"' and not single:
            double = not double
        i += 1
    return single or double


def run_tools(calls: list[dict], master: Any) -> list[dict]:
    """Alle Calls der Reihe nach ausführen. Liefert [{name, args, result}, ...]."""
    out = []
    for call in calls or []:
        name = call.get("name", "")
        args = call.get("args") or {}
        result = run_one(call, master)
        out.append({"name": name, "args": args, "result": result})
    return out
