"""AETHER Supervisor + AgentFactory (MVP Schritt 1+2, 2026-11-02).

Qwen (qwen3.5:9b, lokal) entscheidet nur per JSON (self/spawn/call/final).
Eine deterministische AgentFactory baut Worker NUR aus festen Templates
(researcher, fileops, reviewer). Freie Specs: Feature-Flag ALLOW_FREE_SPECS=False.
PolicyGuard prueft unter dem Modell: Tool erlaubt, Tiefe, Netzwerk, Destruktiv->approval_id.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
import threading
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "workspace" / "aether_supervisor"
OUT_DIR = WORK / "out"            # einziger SAFE_WRITE-Bereich
LOG = ROOT / "workspace" / "logs" / "aether_supervisor.jsonl"
APPROVALS = WORK / "approvals.json"
SANDBOX = ROOT / "workspace" / "sandbox"   # Team-Sandbox: frei arbeiten, kein approval
BACKUP_DIR = ROOT / "workspace" / "backups" / "aether_special"
MODEL = "qwen3.5:9b"
TEMPERATURE = 0.2
LLM_TIMEOUT = 120
RUN_TIMEOUT = 600
SUPERVISOR_MAX_STEPS = 8
MAX_AGENTS = 6
MAX_DEPTH = 2
MAX_TOOLS_PER_WORKER = 8
ALLOW_FREE_SPECS = False          # MVP Schritt 3 (freie Specs) bewusst AUS
_LOCK = threading.Lock()

_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|passw(or)?d|bearer|authorization)\s*[:=]\s*\S+|sk-[A-Za-z0-9]{12,}|gh[pousr]_[A-Za-z0-9]{20,}")
_SECRET_FILE_RE = re.compile(r"(?i)(\.env$|secret|token|credential|passw|\.pem$|\.key$|id_rsa|cookies?)")


def redact(text: Any) -> str:
    return _SECRET_RE.sub("[REDACTED]", str(text))


def log(event: str, **data: Any) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event}
        rec.update({k: json.loads(redact(json.dumps(v, ensure_ascii=False, default=str))) if not isinstance(v, str) else redact(v)[:4000] for k, v in data.items()})
        with _LOCK, open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


# ---------------- Tools (Least Privilege) ----------------
def _safe_path(p: str, base: Path = ROOT) -> Path:
    q = (base / p).resolve() if not os.path.isabs(p) else Path(p).resolve()
    if not str(q).lower().startswith(str(base.resolve()).lower()):
        raise PermissionError("PATH_OUTSIDE_ROOT")
    if _SECRET_FILE_RE.search(q.name):
        raise PermissionError("SECRET_FILE_DENIED")
    return q


def t_list_files(path: str = ".", limit: int = 50) -> dict:
    p = _safe_path(path)
    items = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())[: min(int(limit), 200)]
    return {"path": str(p), "items": items}


def t_read_file(path: str, max_chars: int = 4000) -> dict:
    p = _safe_path(path)
    if p.stat().st_size > 2_000_000:
        raise ValueError("FILE_TOO_LARGE")
    return {"path": str(p), "content": redact(p.read_text(encoding="utf-8", errors="replace")[: min(int(max_chars), 8000)])}


def t_search_files(query: str, path: str = "docs", limit: int = 10) -> dict:
    base = _safe_path(path)
    q = str(query).lower()
    hits: list[str] = []
    for fp in base.rglob("*"):
        if len(hits) >= min(int(limit), 30):
            break
        if any(s in fp.parts for s in ("__pycache__", ".git", "node_modules", "backups")) or not fp.is_file():
            continue
        if fp.suffix.lower() not in (".md", ".txt", ".py", ".json", ".ps1", ".js", ".html", ".yaml", ".yml") or _SECRET_FILE_RE.search(fp.name):
            continue
        if q in fp.name.lower():
            hits.append(str(fp.relative_to(ROOT))); continue
        try:
            if fp.stat().st_size < 500_000 and q in fp.read_text(encoding="utf-8", errors="ignore").lower():
                hits.append(str(fp.relative_to(ROOT)))
        except OSError:
            pass
    return {"query": query, "hits": hits}


def t_search_memory(query: str, limit: int = 5) -> dict:
    from nexus.memory.store import MemoryStore
    res = MemoryStore().search(str(query), limit=min(int(limit), 10))
    return {"query": query, "items": [{"id": i.get("id"), "text": redact(i.get("text", ""))[:500], "score": i.get("score")} for i in res]}


def t_write_note(name: str, content: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}\.(md|txt)", str(name)):
        raise ValueError("BAD_NAME")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = _safe_path(name, OUT_DIR)
    p.write_text(str(content)[:20000], encoding="utf-8")
    return {"written": str(p)}


def t_delete_temp(name: str) -> dict:
    p = _safe_path(str(name), OUT_DIR)
    if not p.is_file():
        raise FileNotFoundError("NOT_FOUND")
    p.unlink()
    return {"deleted": str(p)}


def _sandbox_path(p: str) -> Path:
    """Strikt in SANDBOX: normalisiert, kein .., kein Symlink/Junction auf dem Weg."""
    base = SANDBOX.resolve()
    raw = Path(str(p))
    if raw.is_absolute() or raw.drive or ".." in raw.parts:
        raise PermissionError("SANDBOX_ESCAPE")
    q = (base / raw)
    cur = base
    for part in raw.parts:
        cur = cur / part
        if cur.is_symlink() or (cur.exists() and os.path.realpath(cur) != str(cur.resolve())):
            raise PermissionError("SANDBOX_SYMLINK")
    if not q.resolve().is_relative_to(base) or q.resolve() == base:
        raise PermissionError("SANDBOX_ESCAPE")
    return q.resolve()


def t_sandbox_write(path: str, content: str) -> dict:
    p = _sandbox_path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(content)[:200000], encoding="utf-8"); return {"written": str(p)}


def t_sandbox_delete(path: str) -> dict:
    import shutil
    p = _sandbox_path(path)
    if p.is_dir(): shutil.rmtree(p)
    elif p.is_file(): p.unlink()
    else: raise FileNotFoundError("NOT_FOUND")
    return {"deleted": str(p)}


def t_project_write(path: str, content: str) -> dict:
    """Nur im Spezialauftrag: Datei unter NEXUS-Root schreiben, vorher Backup."""
    p = _safe_path(path)
    if not str(p).lower().startswith("g:\\") and os.name == "nt":
        raise PermissionError("DRIVE_DENIED")
    if p.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        bk = BACKUP_DIR / (time.strftime("%Y%m%d_%H%M%S_") + p.name)
        bk.write_bytes(p.read_bytes())
    else:
        bk = None
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(str(content)[:200000], encoding="utf-8")
    return {"written": str(p), "backup": str(bk) if bk else None}


# --- 2026-11-02 fix2: deterministische Sandbox-Vorgabe + Ergebnispruefung ---
_SANDBOX_WORD = re.compile(r"(?i)\bsandbox\b")
_FILE_RE = re.compile(r"\b([A-Za-z0-9_\-]{1,80}\.(?:txt|md|json|csv|log|py|ps1|html))\b")
_CONTENT_RE = re.compile(r"(?i)(?:mit\s+(?:dem\s+)?Inhalt|Inhalt:?)\s+[\"'„“]?([^\"'“”\n]+?)[\"'“”]?(?:\s+(?:an|und|anlegen|schreiben)\b|[.,;]|$)")
_NON_SANDBOX_WRITE = {"write_note", "project_write"}


def task_targets(task: str) -> dict:
    t = str(task or "")
    m = _CONTENT_RE.search(t)
    return {"sandbox": bool(_SANDBOX_WORD.search(t)), "files": _FILE_RE.findall(t),
            "content": m.group(1).strip() if m else None,
            "delete": bool(re.search(r"(?i)\bl(oe|ö)sch", t))}


def verify_result(task: str) -> dict:
    """Deterministisch: geforderte Datei am geforderten Ort (Sandbox) vorhanden + Inhalt."""
    tg = task_targets(task)
    if not (tg["sandbox"] and tg["files"]):
        return {"checked": False, "ok": True, "reason": "keine pruefbare Pfadvorgabe"}
    for name in tg["files"]:
        try:
            p = _sandbox_path(name)
        except PermissionError as e:
            return {"checked": True, "ok": False, "reason": f"{name}: {e}"}
        if tg["delete"]:
            if p.exists():
                return {"checked": True, "ok": False, "reason": f"{p} existiert noch"}
            continue
        if not p.is_file():
            return {"checked": True, "ok": False, "reason": f"{p} fehlt - Datei muss mit sandbox_write in workspace/sandbox liegen"}
        if tg["content"] is not None and p.read_text(encoding="utf-8", errors="replace").strip() != tg["content"]:
            return {"checked": True, "ok": False, "reason": f"{p}: Inhalt passt nicht"}
    return {"checked": True, "ok": True, "reason": "Datei(en) am geforderten Ort geprueft", "files": tg["files"]}


# --- 2026-11-02 fix3: Web (vorhandene NEXUS-Research, DuckDuckGo ohne Key) + Terminal (bestehendes shell_run-Gate) ---
WEB_TIMEOUT = 20


def t_web_search(query: str, limit: int = 5) -> dict:
    q = str(query)
    if _SECRET_RE.search(q):
        raise PermissionError("SECRET_IN_QUERY")
    from nexus.knowledge.research import Research
    r = Research().search(q[:200], limit=min(int(limit), 8))
    return {"success": bool(r.get("success")), "error": r.get("error"),
            "results": [{"title": x.get("title"), "url": x.get("url"), "snippet": str(x.get("snippet") or "")[:300]} for x in (r.get("results") or [])][:8]}


def t_fetch_url(url: str, max_chars: int = 4000) -> dict:
    import urllib.parse, ipaddress, socket
    u = str(url)
    pr = urllib.parse.urlparse(u)
    if pr.scheme not in ("http", "https") or not pr.hostname or _SECRET_RE.search(u) or pr.username or pr.password:
        raise PermissionError("URL_DENIED")
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(pr.hostname))
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise PermissionError("PRIVATE_NET_DENIED")
    except (socket.gaierror, ValueError):
        raise PermissionError("DNS_FAIL")
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 NEXUS-Aether"})
    with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as r:
        raw = r.read(600_000).decode("utf-8", errors="replace")
    txt = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", raw)
    txt = re.sub(r"<[^>]+>", " ", txt); txt = re.sub(r"\s+", " ", txt)
    return {"url": u, "text": txt[: min(int(max_chars), 8000)]}


_SHELL_MUTATE = re.compile(r"(?i)(>|\b(remove-item|rm|del|erase|rd|rmdir|ri|clear-content|clear-item|move-item|mv|move|mi|rename-item|ren|rni|"
                           r"set-content|sc|add-content|ac|out-file|new-item|ni|mkdir|md|copy-item|cp|copy|cpi|set-item|si|tee-object|tee|"
                           r"expand-archive|compress-archive|robocopy|xcopy|attrib|takeown|format|cipher|git)\b)")
_SHELL_ESCAPE = re.compile(r"(?i)([a-z]:|\.\.|^[\\/]|\s[\\/~]|~|\$|%|\b(set-location|cd|chdir|sl|push-location|pushd|invoke-expression|iex|start-process|cmd|powershell|pwsh)\b|\[|`)")


def _sandbox_shell_check(command: str) -> str | None:
    """Aether-Vorpruefung VOR dem Gate: veraendernde Befehle nur mit relativen Pfaden in der Sandbox."""
    c = str(command)
    if _SHELL_MUTATE.search(c) and _SHELL_ESCAPE.search(c):
        return "AETHER_WRITE_OUTSIDE_SANDBOX: veraendernde Befehle nur mit relativen Pfaden in workspace/sandbox"
    return None


def t_shell_run(command: str, approval_id: str | None = None, timeout: int = 60) -> dict:
    """Ueber das BESTEHENDE Gate (safety.dispatch -> Registry shell_run -> terminal.service + guard).
    Stufe kommt aus der Terminal-Konfiguration fuer Agent AETHER, nicht von hier. cwd = Sandbox."""
    why = None if approval_id else _sandbox_shell_check(command)  # mit approval_id entscheidet das Gate
    if why:
        log("shell_precheck_blocked", command=command, reason=why)
        return {"ok": False, "success": False, "status": "BLOCKED", "error": why, "stdout": "", "stderr": ""}
    from nexus.safety.dispatch import shell_execute
    SANDBOX.mkdir(parents=True, exist_ok=True)
    r = shell_execute(str(command), agent="AETHER", shell="powershell", cwd=str(SANDBOX), timeout=min(int(timeout), 120),
                      approval_id=approval_id)
    keep = ("ok", "success", "status", "error", "verdict", "reasons", "returncode", "run", "level_agent", "level_required")
    out = {k: r.get(k) for k in keep if k in r}
    out["stdout"] = redact(str(r.get("stdout") or ""))[:3000]
    out["stderr"] = redact(str(r.get("stderr") or ""))[:1000]
    return out


@dataclass
class ToolDef:
    fn: Callable[..., dict]
    level: str            # READ_ONLY | SAFE_WRITE | PRIVILEGED
    destructive: bool = False
    network: bool = False
    desc: str = ""
    special_only: bool = False


TOOLS: dict[str, ToolDef] = {
    "list_files": ToolDef(t_list_files, "READ_ONLY", desc="list_files(path) Ordner unter NEXUS-Root auflisten"),
    "read_file": ToolDef(t_read_file, "READ_ONLY", desc="read_file(path, max_chars) Textdatei unter NEXUS-Root lesen"),
    "search_files": ToolDef(t_search_files, "READ_ONLY", desc="search_files(query, path) Text in Dateien unter path suchen (Default docs)"),
    "search_memory": ToolDef(t_search_memory, "READ_ONLY", desc="search_memory(query) NEXUS-Gedaechtnis durchsuchen"),
    "write_note": ToolDef(t_write_note, "SAFE_WRITE", desc="write_note(name, content) Notiz nach workspace/aether_supervisor/out schreiben"),
    "delete_temp": ToolDef(t_delete_temp, "PRIVILEGED", destructive=True, desc="delete_temp(name) Datei in workspace/aether_supervisor/out loeschen (nur mit approval_id)"),
    "sandbox_write": ToolDef(t_sandbox_write, "SAFE_WRITE", desc="sandbox_write(path, content) frei in workspace/sandbox schreiben (relativer Pfad)"),
    "sandbox_delete": ToolDef(t_sandbox_delete, "SAFE_WRITE", desc="sandbox_delete(path) frei in workspace/sandbox loeschen (relativer Pfad)"),
    "web_search": ToolDef(t_web_search, "READ_ONLY", network=True, desc="web_search(query) kostenlose Websuche (DuckDuckGo), liefert Titel/URL/Snippet"),
    "fetch_url": ToolDef(t_fetch_url, "READ_ONLY", network=True, desc="fetch_url(url) oeffentliche Webseite als Text lesen"),
    "shell_run": ToolDef(t_shell_run, "SAFE_WRITE", desc="shell_run(command) PowerShell-Befehl ueber das NEXUS-Terminal-Gate, cwd=workspace/sandbox"),
    "project_write": ToolDef(t_project_write, "SAFE_WRITE", special_only=True, desc="project_write(path, content) nur im Spezialauftrag: Datei unter NEXUS-Root schreiben, mit Backup"),
}
# Netzwerk nur fuer Agenten in NETWORK_AGENTS (PolicyGuard).
NETWORK_AGENTS = {"researcher"}


# ---------------- Approvals (Human-in-the-Loop) ----------------
def _load_approvals() -> dict:
    try:
        return json.loads(APPROVALS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def grant_approval(tool: str, args: dict, ttl_s: int = 900) -> str:
    """Nur vom lokalen Menschen (Endpoint prueft localhost). Einmal verwendbar."""
    aid = "AP-" + uuid.uuid4().hex[:12]
    with _LOCK:
        data = _load_approvals()
        data[aid] = {"tool": tool, "args": args, "expires": time.time() + ttl_s, "used": False}
        WORK.mkdir(parents=True, exist_ok=True)
        APPROVALS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log("approval_granted", approval_id=aid, tool=tool, args=args)
    return aid


def _consume_approval(aid: str | None, tool: str, args: dict) -> bool:
    if not aid:
        return False
    with _LOCK:
        data = _load_approvals()
        a = data.get(aid)
        if not a or a.get("used") or a.get("expires", 0) < time.time() or a.get("tool") != tool or a.get("args") != args:
            return False
        a["used"] = True
        APPROVALS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


# ---------------- PolicyGuard ----------------
class PolicyDenied(Exception):
    pass


class PolicyGuard:
    """Deterministisch, unter dem Modell. Kein Bypass."""
    def __init__(self, max_depth: int = MAX_DEPTH, allow_network: bool = False):
        self.max_depth, self.allow_network = max_depth, allow_network

    def check(self, spec: "AgentSpec", tool: str, args: dict, depth: int, approval_id: str | None = None, special: bool = False) -> None:
        if tool not in spec.tools:
            raise PolicyDenied("TOOL_NOT_IN_SPEC")
        td = TOOLS.get(tool)
        if td is None:
            raise PolicyDenied("UNKNOWN_TOOL")
        if depth > self.max_depth:
            raise PolicyDenied("DEPTH_LIMIT")
        if td.special_only and not special:
            raise PolicyDenied("SPECIAL_ORDER_REQUIRED")
        if td.network and not (self.allow_network or spec.name in NETWORK_AGENTS):
            raise PolicyDenied("NETWORK_DENIED")
        if (td.destructive or td.level == "PRIVILEGED") and not _consume_approval(approval_id, tool, args):
            raise PolicyDenied("APPROVAL_REQUIRED")


# ---------------- AgentSpec / Templates / Factory ----------------
@dataclass
class AgentSpec:
    name: str
    role: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    max_steps: int = 8
    can_spawn: bool = False


TEMPLATES: dict[str, dict] = {
    "researcher": dict(name="researcher", role="Lokale Recherche: Gedaechtnis und NEXUS-Dateien durchsuchen, Quellen nennen.",
                       system_prompt="Du bist researcher. Nutze nur deine Tools. Antworte auf Deutsch mit Quellen (Dateipfad oder Memory-ID) und Kurzfazit. Erfinde nichts; wenn nichts gefunden, sag das.",
                       tools=["search_memory", "search_files", "read_file", "list_files", "web_search", "fetch_url"], max_steps=6),
    "terminal": dict(name="terminal", role="Befehle im NEXUS-Terminal ausfuehren (Gate entscheidet Rechte), Arbeitsordner Sandbox.",
                     system_prompt="Du bist terminal. Nutze shell_run fuer PowerShell-Befehle. Das Gate entscheidet; BLOCKED-Ergebnisse ehrlich melden, nicht umgehen. Kein C:. Gib die echte Ausgabe wieder.",
                     tools=["shell_run", "list_files", "read_file"], max_steps=5),
    "fileops": dict(name="fileops", role="Dateien auflisten/lesen, Notizen in den Ausgabeordner schreiben. Loeschen nur mit Freigabe.",
                    system_prompt="Du bist fileops. Nutze nur deine Tools. In workspace/sandbox (sandbox_write/sandbox_delete) arbeitest du frei. project_write nur im Spezialauftrag. Schreiben nur per write_note. delete_temp braucht eine Freigabe von Eddie; ohne Freigabe nicht erneut versuchen. Antworte kurz auf Deutsch.",
                    tools=["list_files", "read_file", "write_note", "delete_temp", "sandbox_write", "sandbox_delete", "project_write"], max_steps=6),
    "reviewer": dict(name="reviewer", role="Prueft eine Antwort auf Belegbarkeit und Vollstaendigkeit. Keine Tools.",
                     system_prompt="Du bist reviewer. Pruefe die Antwort gegen Aufgabe und Belege. ok=true nur wenn die Antwort die Aufgabe beantwortet und nichts Unbelegtes behauptet. Antworte als JSON.",
                     tools=[], max_steps=1),
}


class FactoryError(ValueError):
    pass


class AgentFactory:
    def __init__(self, llm: "OllamaJSON", tools: dict[str, ToolDef] | None = None, guard: PolicyGuard | None = None,
                 max_agents: int = MAX_AGENTS, max_depth: int = MAX_DEPTH, allow_free_specs: bool = ALLOW_FREE_SPECS):
        self.llm, self.tools = llm, tools if tools is not None else TOOLS
        self.guard = guard or PolicyGuard(max_depth)
        self.agents: dict[str, WorkerAgent] = {}
        self.max_agents, self.max_depth, self.allow_free_specs = max_agents, max_depth, allow_free_specs

    def validate(self, raw: dict | str, depth: int = 0) -> AgentSpec:
        if isinstance(raw, str):
            raw = {"template": raw}
        if not isinstance(raw, dict):
            raise FactoryError("SPEC_NOT_OBJECT")
        tpl = raw.get("template") or raw.get("name")
        if tpl in TEMPLATES and (raw.get("template") or set(raw) <= {"name"} or self._same_as_template(raw)):
            spec = AgentSpec(**TEMPLATES[tpl])
        elif not self.allow_free_specs:
            raise FactoryError("FREE_SPEC_DISABLED")
        else:
            try:
                spec = AgentSpec(**{k: raw[k] for k in raw if k in AgentSpec.__dataclass_fields__})
            except TypeError as e:
                raise FactoryError("SPEC_INVALID") from e
        if len(self.agents) >= self.max_agents and spec.name not in self.agents:
            raise FactoryError("AGENT_LIMIT")
        if not spec.name.isidentifier():
            raise FactoryError("BAD_NAME")
        if set(spec.tools) - set(self.tools):
            raise FactoryError("UNKNOWN_TOOL")
        if len(spec.tools) > MAX_TOOLS_PER_WORKER:
            raise FactoryError("TOO_MANY_TOOLS")
        if depth > self.max_depth:
            raise FactoryError("DEPTH_LIMIT")
        if depth >= self.max_depth:
            spec.can_spawn = False
        spec.max_steps = max(1, min(int(spec.max_steps), 8))
        return spec

    @staticmethod
    def _same_as_template(raw: dict) -> bool:
        t = TEMPLATES[raw["name"]]
        return all(raw.get(k, t[k]) == t[k] for k in ("role", "system_prompt", "tools"))

    def spawn(self, raw: dict | str, depth: int = 0) -> "WorkerAgent":
        spec = self.validate(raw, depth)
        if spec.name in self.agents:
            return self.agents[spec.name]
        a = WorkerAgent(spec, self.llm, {t: self.tools[t] for t in spec.tools}, depth, self.guard)
        self.agents[spec.name] = a
        log("spawn", spec=asdict(spec), depth=depth)
        return a


# ---------------- LLM (Ollama, JSON-Schema erzwungen) ----------------
class OllamaJSON:
    def __init__(self, model: str = MODEL, temperature: float = TEMPERATURE, timeout: int = LLM_TIMEOUT):
        self.model, self.temperature, self.timeout = model, temperature, timeout

    def json(self, system: str, user: str, schema: dict) -> dict:
        from nexus.common.ai_layer import keep_alive_value, load_config, ollama_base_url, unified_num_ctx
        cfg = load_config()
        payload = {"model": self.model, "stream": False, "think": False, "format": schema,
                   "keep_alive": keep_alive_value(cfg),
                   "options": {"num_ctx": unified_num_ctx(), "temperature": self.temperature},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        req = urllib.request.Request(ollama_base_url(cfg) + "/api/chat", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return json.loads((data.get("message") or {}).get("content") or "{}")


WORKER_SCHEMA = {"type": "object", "properties": {
    "tool": {"type": "string"}, "args": {"type": "object"}, "answer": {"type": "string"}},
    "required": ["tool", "args", "answer"]}
REVIEW_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["ok", "reason"]}


class WorkerAgent:
    def __init__(self, spec: AgentSpec, llm: OllamaJSON, tools: dict[str, ToolDef], depth: int, guard: PolicyGuard):
        self.spec, self.llm, self.tools, self.depth, self.guard = spec, llm, tools, depth, guard

    def run(self, task: str, approval_id: str | None = None, deadline: float | None = None, special: bool = False) -> str:
        if not self.tools:  # z. B. reviewer
            r = self.llm.json(self.spec.system_prompt, task, REVIEW_SCHEMA if self.spec.name == "reviewer" else WORKER_SCHEMA)
            log("worker_result", agent=self.spec.name, result=r)
            return json.dumps(r, ensure_ascii=False)
        sbx = task_targets(task)["sandbox"]
        offered = {n: d for n, d in self.tools.items() if not (sbx and n in _NON_SANDBOX_WRITE)}
        tool_list = "\n".join(f"- {n}: {t.desc}" for n, t in offered.items())
        if sbx:
            tool_list += "\nSANDBOX-AUFTRAG: schreibe/loesche nur mit sandbox_write/sandbox_delete, path relativ zu workspace/sandbox."
        system = (self.spec.system_prompt + "\nTools:\n" + tool_list +
                  "\nAntworte NUR als JSON {tool, args, answer}. Fuer ein Tool: tool=<name>, answer=\"\". "
                  "Wenn fertig: tool=\"done\", args={}, answer=<Ergebnis mit Quellen>.")
        transcript = "Aufgabe: " + task
        for step in range(self.spec.max_steps):
            if deadline and time.time() > deadline:
                return "TIMEOUT"
            d = self.llm.json(system, transcript, WORKER_SCHEMA)
            tool, args = str(d.get("tool") or "done"), d.get("args") if isinstance(d.get("args"), dict) else {}
            if tool == "done":
                log("worker_result", agent=self.spec.name, step=step, answer=d.get("answer", ""))
                return str(d.get("answer") or "")
            try:
                if tool not in offered and tool in self.tools:
                    raise PolicyDenied("SANDBOX_TASK_USE_SANDBOX_TOOLS")
                self.guard.check(self.spec, tool, args, self.depth, approval_id, special)
                if tool == "shell_run" and approval_id and "approval_id" not in args:
                    args = dict(args, approval_id=approval_id)
                res = self.tools[tool].fn(**args)
                out = json.dumps(res, ensure_ascii=False)[:3000]
                log("tool_call", agent=self.spec.name, tool=tool, args=args, ok=True, result=out[:1500])
            except PolicyDenied as e:
                out = f"POLICY_DENIED: {e}"
                log("tool_call", agent=self.spec.name, tool=tool, args=args, ok=False, denied=str(e))
            except Exception as e:  # Toolfehler offen zurueckgeben
                out = f"TOOL_ERROR: {type(e).__name__}: {e}"
                log("tool_call", agent=self.spec.name, tool=tool, args=args, ok=False, error=out)
            transcript += f"\n\n[{tool} {json.dumps(args, ensure_ascii=False)}]\n{out}"
        return "MAX_STEPS"


# ---------------- Supervisor ----------------
SUPERVISOR_SCHEMA = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["self", "spawn", "call", "final"]},
    "template": {"type": "string", "enum": ["researcher", "fileops", "terminal", "none"]},
    "agent": {"type": "string"}, "task": {"type": "string"}, "answer": {"type": "string"}},
    "required": ["action", "template", "agent", "task", "answer"]}

SUPERVISOR_PROMPT = (
    "Du bist AETHER, Supervisor in NEXUS, Kommandoton, kurz und bestimmt, ehrlich, schadest Eddie nie. Du schreibst keinen Code und fuehrst selbst nichts aus, du entscheidest. "
    "Verfuegbare Worker-Templates: researcher (Gedaechtnis, NEXUS-Dateien, kostenlose Websuche web_search/fetch_url), terminal (PowerShell ueber NEXUS-Gate, cwd Sandbox), "
    "fileops (Dateien auflisten/lesen, Notiz schreiben, Loeschen nur mit Freigabe). "
    "Felder immer: action, template (researcher|fileops|terminal|none), agent, task, answer. Aktionen: spawn (template=researcher, fileops oder terminal + task: kurze Teilaufgabe), call (agent + task fuer bereits gestarteten Worker), "
    "self (kurz nachdenken, answer=Notiz), final (answer=Endantwort auf Deutsch, nur Belegtes, mit Quellen aus Worker-Ergebnissen). "
    "Gib einem Worker nur seine Teilaufgabe. Wenn Ergebnisse reichen: final.")


def _carry(sub: str, task: str) -> str:
    tg = task_targets(task)
    if tg["sandbox"] and not task_targets(sub)["sandbox"]:
        sub += " (Ziel: Sandbox, workspace/sandbox" + (", Datei " + ", ".join(tg["files"]) if tg["files"] else "") + ")"
    return sub


def run_supervisor(task: str, llm: OllamaJSON | None = None, factory: AgentFactory | None = None,
                   max_steps: int = SUPERVISOR_MAX_STEPS, approval_id: str | None = None, timeout_s: int = RUN_TIMEOUT, special: bool = False) -> dict:
    llm = llm or OllamaJSON()
    factory = factory or AgentFactory(llm)
    run_id = "SV-" + uuid.uuid4().hex[:10]
    t0 = time.time(); deadline = t0 + timeout_s
    ctx = "Aufgabe von Eddie: " + str(task).strip()
    trace: list[dict] = []
    retried = False
    log("run_start", run_id=run_id, task=task, special_order=special)
    if special:
        ctx += "\n[SPEZIALAUFTRAG von Eddie: erweiterter Spielraum, project_write erlaubt. Harte Grenzen bleiben: C: tabu, Eddie nie schaden, Backup, keine Secrets, kein Bypass.]"
    for step in range(max_steps):
        if time.time() > deadline:
            break
        try:
            d = llm.json(SUPERVISOR_PROMPT, ctx, SUPERVISOR_SCHEMA)
        except Exception as e:
            log("run_end", run_id=run_id, status="FAIL", error=type(e).__name__)
            return {"success": False, "run_id": run_id, "error": "SUPERVISOR_LLM_ERROR", "error_type": type(e).__name__, "trace": trace}
        act = d.get("action")
        log("decision", run_id=run_id, step=step, decision=d)
        entry: dict[str, Any] = {"step": step, "action": act}
        try:
            if act == "spawn":
                spec_raw = d.get("spec") if isinstance(d.get("spec"), dict) else {"template": next((x for x in (d.get("template"), d.get("agent")) if x in TEMPLATES and x != "reviewer"), "")}
                w = factory.spawn(spec_raw, depth=1)
                r = w.run(_carry(str(d.get("task") or task), task), approval_id, deadline, special)
                entry.update(agent=w.spec.name, result=r[:1500]); ctx += f"\n\n[{w.spec.name}]\n{r}"
            elif act == "call":
                name = str(d.get("agent") or d.get("template") or "")
                if name not in factory.agents:
                    raise FactoryError("AGENT_NOT_SPAWNED")
                r = factory.agents[name].run(_carry(str(d.get("task") or task), task), approval_id, deadline, special)
                entry.update(agent=name, result=r[:1500]); ctx += f"\n\n[{name}]\n{r}"
            elif act == "self":
                entry.update(note=str(d.get("answer"))[:500]); ctx += f"\n\n[notiz]\n{d.get('answer')}"
            elif act == "final":
                answer = str(d.get("answer") or "")
                vr = verify_result(task)
                log("verify", run_id=run_id, result=vr)
                if not vr["ok"]:
                    rr = {"ok": False, "reason": "DETERMINISTISCH FAIL: " + vr["reason"], "deterministic": vr}
                else:
                    rv = factory.spawn("reviewer", depth=1)
                    rr = json.loads(rv.run(f"Aufgabe: {task}\n\nBelege/Kontext:\n{ctx[-5000:]}\n\nAntwort:\n{answer}", deadline=deadline))
                    rr["deterministic"] = vr
                entry.update(answer=answer, review=rr); trace.append(entry)
                if rr.get("ok") or retried:
                    status = "PASS" if rr.get("ok") else ("FAIL" if not vr["ok"] else "PARTIAL")
                    if status == "FAIL":
                        log("run_end", run_id=run_id, status="FAIL", answer=answer, review=rr)
                        return {"success": False, "run_id": run_id, "error": "VERIFY_FAILED", "answer": answer, "review": rr, "review_status": "FAIL", "retried": True, "trace": trace, "agents": list(factory.agents)}
                    log("run_end", run_id=run_id, status=status, answer=answer, review=rr, secs=round(time.time() - t0, 1))
                    return {"success": True, "run_id": run_id, "answer": answer, "review": rr, "review_status": status,
                            "retried": retried, "trace": trace, "agents": list(factory.agents), "secs": round(time.time() - t0, 1)}
                retried = True
                ctx += f"\n\n[reviewer: abgelehnt]\n{rr.get('reason')}\nVerbessere die Antwort (ein Versuch)."
                continue
            else:
                raise FactoryError("UNKNOWN_ACTION")
        except (FactoryError, PolicyDenied) as e:
            entry.update(error=str(e)); ctx += f"\n\n[system]\nABGELEHNT: {e}"
            log("rejected", run_id=run_id, step=step, error=str(e))
        except Exception as e:
            entry.update(error=f"{type(e).__name__}"); ctx += f"\n\n[system]\nFEHLER: {type(e).__name__}"
            log("error", run_id=run_id, step=step, error=f"{type(e).__name__}: {e}")
        trace.append(entry)
    log("run_end", run_id=run_id, status="MAX_STEPS")
    return {"success": False, "run_id": run_id, "error": "MAX_STEPS_OR_TIMEOUT", "trace": trace, "agents": list(factory.agents)}
