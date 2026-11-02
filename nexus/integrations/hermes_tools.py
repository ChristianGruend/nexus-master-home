"""Hermes native Tool-Loop (2026-10-26, Freigabe Eddie 'Ja, Hermes reparieren').

Ursache: Der Hermes-CLI-Lauf (OpenAI-Stream, ~5,5k Token Systemprompt) liefert bei qwen3.5 den
Werkzeugaufruf nur als TEXT (z. B. ``read_file(path="...")``), tool_turns=0. Ollama selbst kann
native tool_calls (/api/chat, belegt 26.10.). Dieses Modul erkennt Text-Toolcalls und fuehrt den
Auftrag dann ueber Ollama-native Tools aus - echt, mit Sicherheitspruefung:
- Pfade nur unter <ROOT>/workspace, keine Geheimnisdateien
- write_file nur im Hermes-Arbeitsverzeichnis, max 200 kB
- jeder Aufruf in workspace/hermes_service/tool_calls.log
"""
from __future__ import annotations

import fnmatch, json, re, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WS = ROOT / "workspace"
LOG = WS / "hermes_service" / "tool_calls.log"
OLLAMA = "http://127.0.0.1:11434"
MAX_TURNS = 6
GITHUB_OWNER_HERMES_SEARCH_LIMIT = 5
MAX_READ = 20000
MAX_WRITE = 200_000
SECRET = re.compile(r"(\.env$|secret|token|credential|passw|\.key$|\.pem$|id_rsa)", re.I)
TEXT_CALL = re.compile(r"\b(read_file|write_file|list_directory|list_files|search_files|patch)\s*\(", re.I)

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Textdatei lesen",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "list_directory", "description": "Verzeichnisinhalt auflisten",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "search_files", "description": "Dateinamen per Muster suchen (z. B. *.txt)",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Textdatei im Arbeitsverzeichnis schreiben",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "github", "description": "GitHub per gh-CLI (eingeloggt). command MUSS mit 'gh' oder 'git' beginnen. Beispiele: 'gh search repos NAME --limit 5', 'gh repo view OWNER/REPO' (zeigt README), 'gh repo clone OWNER/REPO G:\\meine-ki\\nexus-ai\\workspace\\sandbox\\repos\\REPO'. Lesen frei, Schreiben SAFE_WRITE, Push main/Loeschen nur mit approval_id.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}, "approval_id": {"type": "string"}}, "required": ["command"]}}},
]


WANTS = re.compile(r"\b(read_file|write_file|list_directory|search_files|github|gh|repo|issue|pull request)\b", re.I)


def wants_tools(task: str) -> bool:
    return bool(task) and bool(WANTS.search(task))


def has_text_toolcall(reply: str) -> bool:
    return bool(reply) and bool(TEXT_CALL.search(reply))


def _log(e: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


def safe_path(p: str, wd: Path, write: bool = False) -> Path:
    q = Path(p or ".")
    q = (q if q.is_absolute() else wd / q).resolve()
    base = wd.resolve() if write else WS.resolve()
    try:
        q.relative_to(base)
    except ValueError:
        raise PermissionError(f"PATH_NOT_AUTHORIZED: {q} ausserhalb {base}")
    if SECRET.search(q.name):
        raise PermissionError(f"SECRET_FILE_BLOCKED: {q.name}")
    return q


def run_tool(name: str, args: dict, wd: Path) -> str:
    if name == "read_file":
        p = safe_path(args.get("path", ""), wd)
        return p.read_text(encoding="utf-8-sig", errors="replace")[:MAX_READ]
    if name in ("list_directory", "list_files"):
        p = safe_path(args.get("path", "."), wd)
        return "\n".join(sorted((x.name + ("/" if x.is_dir() else "")) for x in p.iterdir())[:200])
    if name == "search_files":
        p = safe_path(args.get("path", "."), wd)
        pat = args.get("pattern", "*")
        hits = [str(x.relative_to(WS)) for x in p.rglob("*") if x.is_file() and fnmatch.fnmatch(x.name, pat)][:100]
        return "\n".join(hits) or "(keine Treffer)"
    if name == "write_file":
        p = safe_path(args.get("path", ""), wd, write=True)
        c = str(args.get("content", ""))
        if len(c.encode("utf-8")) > MAX_WRITE:
            raise ValueError("WRITE_TOO_LARGE")
        if p.exists():
            bak = p.with_name(p.name + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
            bak.write_bytes(p.read_bytes())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(c, encoding="utf-8")
        return f"geschrieben: {p} ({len(c)} Zeichen)"
    if name == "github":
        from nexus.integrations import github_tool
        r = github_tool.run(str(args.get("command", "")), agent="Hermes", cwd=args.get("cwd"), approval_id=args.get("approval_id"))
        return json.dumps({k: r.get(k) for k in ("success", "status", "level", "approval_id", "error", "returncode", "stdout", "stderr")}, ensure_ascii=False)[:MAX_READ]
    raise ValueError(f"UNKNOWN_TOOL: {name}")


def _num_ctx() -> int:
    try:
        from nexus.common.ai_layer import unified_num_ctx
        return int(unified_num_ctx())
    except Exception:
        return 8192


def _keep_alive():
    try:
        from nexus.common.ai_layer import keep_alive_value
        return keep_alive_value()
    except Exception:
        return "30m"


def _chat_body(model: str, messages: list) -> dict:
    # 2026-10-29: gleicher num_ctx/keep_alive wie NEXUS -> kein Modell-Neuladen durch abweichenden Kontext
    return {"model": model, "messages": messages, "tools": TOOLS, "stream": False, "think": False,
            "keep_alive": _keep_alive(), "options": {"num_ctx": _num_ctx()}}


def _chat(model: str, messages: list, timeout: int) -> dict:
    body = json.dumps(_chat_body(model, messages)).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())["message"]


def native_loop(task: str, wd: Path, model: str, rid: str, timeout: int = 170) -> dict:
    t0 = time.time()
    sys = ("Du bist Hermes, der Werkzeug-Agent von NEXUS. Arbeitsverzeichnis: " + str(wd) +
           ". Nutze die bereitgestellten Werkzeuge ueber echte tool_calls, nie als Text. Antworte danach knapp auf Deutsch."
           " GITHUB: Du hast echten Zugriff ueber das Werkzeug 'github' (gh ist eingeloggt, alle Repos von Eddie und alle oeffentlichen Repos lesbar)."
           " Ist ein Repo nur per Name bekannt: 1) gh search repos NAME --limit 5  2) gh repo view OWNER/REPO (liefert README)"
           "  3) bei Bedarf gh repo clone OWNER/REPO G:\\meine-ki\\nexus-ai\\workspace\\sandbox\\repos\\REPO."
           " Frag NIE nach einer URL, wenn du sie selbst suchen kannst. Fasse README-Inhalte nur aus echter Werkzeug-Ausgabe zusammen.")
    msgs = [{"role": "system", "content": sys}, {"role": "user", "content": task}]
    calls = []
    for turn in range(MAX_TURNS):
        left = timeout - (time.time() - t0)
        if left < 10:
            break
        try:
            msg = _chat(model, msgs, int(left))
        except Exception as e:
            if not calls:
                raise
            done = ", ".join(f"{c['tool']}={'ok' if c['ok'] else 'fehler'}" for c in calls)
            return {"reply": f"Werkzeuge ausgefuehrt ({done}); Abschlussantwort des Modells fehlt ({type(e).__name__}).",
                    "tool_calls": calls, "turns": turn, "final_missing": True}
        tcs = msg.get("tool_calls") or []
        msgs.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tcs})
        if not tcs:
            return {"reply": (msg.get("content") or "").strip(), "tool_calls": calls, "turns": turn + 1}
        for tc in tcs:
            fn = tc.get("function", {}); name = fn.get("name", ""); args = fn.get("arguments") or {}
            if isinstance(args, str):
                try: args = json.loads(args)
                except Exception: args = {}
            try:
                res, ok = run_tool(name, args, wd), True
            except Exception as e:
                res, ok = f"{type(e).__name__}: {e}", False
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "request_id": rid, "tool": name, "args": {k: (str(v)[:200]) for k, v in args.items()},
                   "ok": ok, "result_preview": res[:300]}
            _log(rec); calls.append(rec)
            msgs.append({"role": "tool", "tool_name": name, "content": res})
    return {"reply": "HERMES_TOOL_LOOP_LIMIT", "tool_calls": calls, "turns": MAX_TURNS}