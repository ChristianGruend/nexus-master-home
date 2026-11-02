"""NEXUS-Terminal (20261031): gemeinsames Live-Terminal fuer NEXUS, Hermes, Autobuild und Eddie.

- run(): fuehrt einen Befehl aus (PowerShell/cmd/Python), streamt stdout/stderr zeilenweise.
- record(): protokolliert einen woanders ausgefuehrten Lauf (Autobuild-pytest, Hermes-Skripte) ohne dessen Verhalten zu aendern.
Spur: workspace/terminal/terminal.jsonl (Rotation 5 MB -> .1). Jede Zeile ein Event:
  {"id","ts","run","agent","type": start|out|err|end|blocked, ...}
Sperren: Arbeitsordner nur G:/Projekt, keine C:-Pfade, offensichtlich destruktive Befehle, Timeout.
Secrets (token/password/bearer/api_key ...) werden vor dem Schreiben maskiert.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from nexus.common.paths import ROOT

TERM_DIR = ROOT / "workspace" / "terminal"
LOG_FILE = TERM_DIR / "terminal.jsonl"
MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 900
AGENTS = ("NEXUS-GPT", "NEXUS", "Hermes", "Autobuild", "Eddie")
SESSION_AGENTS = ("NEXUS", "Hermes", "NEXUS-GPT", "Eddie")
_LOCK = threading.Lock()
_RUNS: dict[str, subprocess.Popen] = {}
_CANCELLED: set = set()
_ACTIVE: dict[str, dict] = {}  # run_id -> {agent, cmd, ts, shell}

_SECRET = re.compile(r"(?i)(token|password|passwort|passwd|pwd|secret|bearer|api[_-]?key|authorization)([\"'\s:=]+)([^\s\"',}]{4,})")
_SECRET2 = re.compile(r"\b(sk-[A-Za-z0-9_\-]{12,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|gh[usr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abp]-[A-Za-z0-9\-]{10,}|AIza[0-9A-Za-z_\-]{20,}|tskey-[A-Za-z0-9\-]{10,})")

_C_PATH = re.compile(r"(?i)(?<![A-Za-z0-9])c:[\\/]|(?<![A-Za-z0-9])c:(?=\s|$|\"|')|\\\\\?\\c:|\$env:(systemroot|windir|programfiles|userprofile|appdata|localappdata|temp|tmp)\b|%(systemroot|windir|programfiles|userprofile|appdata|localappdata|temp|tmp)%")
_DESTRUCTIVE = [
    (re.compile(r"(?i)\bformat(-volume)?\b\s+[a-z]:?"), "format"),
    (re.compile(r"(?i)\bformat-volume\b"), "format-volume"),
    (re.compile(r"(?i)\b(clear-disk|initialize-disk|remove-partition|clear-partition|set-partition)\b"), "disk"),
    (re.compile(r"(?i)\bdiskpart\b"), "diskpart"),
    (re.compile(r"(?i)\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|-r\s+-f|-f\s+-r)\s+(/|~|\*|\.\s*$|\.\.|[a-z]:[\\/]?\s*$)"), "rm -rf"),
    (re.compile(r"(?i)\b(remove-item|ri|rm|rmdir|rd|del|erase)\b[^|;&]*\s([a-z]:[\\/]?\*?)(\s|$|\"|')"), "Laufwerkswurzel loeschen"),
    (re.compile(r"(?i)\b(remove-item|ri|rm|rmdir|rd|del|erase)\b[^|;&]*\s[\"']?(g:[\\/]meine-ki[\\/]?|g:[\\/]meine-ki[\\/]nexus-ai[\\/]?)[\"']?(\s|$)"), "Projektwurzel loeschen"),
    (re.compile(r"(?i)\b(remove-item|ri|rm|rmdir|rd|del|erase)\b[^|;&]*(\s|[\\/]|[\"'])\.git\b"), ".git loeschen"),
    (re.compile(r"(?i)\bgit\s+(clean\s+-[a-z]*f[a-z]*d|reset\s+--hard|push\s+.*--force|push\s+-f)\b"), "git destruktiv"),
    (re.compile(r"(?i)\b(shutdown|restart-computer|stop-computer|logoff)\b"), "shutdown/restart"),
    (re.compile(r"(?i)\b(bcdedit|vssadmin|wbadmin|cipher\s+/w|reg\s+delete|remove-itemproperty\s+.*hklm|takeown|icacls\s+.*\/grant)\b"), "System"),
    (re.compile(r"(?i)\bmkfs\b|\bdd\s+if=.*of=/dev/"), "mkfs/dd"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"), "forkbomb"),
    (re.compile(r"(?i)\bstop-process\b[^|;&]*(-name\s+\*|\bpython\b|\bollama\b)|\btaskkill\b[^|;&]*/im\s+(python|ollama|explorer|svchost|csrss|winlogon)"), "NEXUS/System-Prozesse beenden"),
    (re.compile(r"(?i)\b(invoke-expression|iex)\b[^|;&]*\b(downloadstring|invoke-webrequest|iwr|irm)\b|\b(iwr|irm|invoke-webrequest|invoke-restmethod)\b[^|;&]*\|\s*(iex|invoke-expression)"), "Download+Ausfuehren"),
    (re.compile(r"(?i)\bshutil\.rmtree\s*\(\s*[\"'](/|[a-z]:[\\/]?)[\"']"), "rmtree Wurzel"),
]


def mask(text) -> str:
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    s = _SECRET.sub(lambda m: m.group(1) + m.group(2) + "***", s)
    return _SECRET2.sub("***", s)


_TERM_CFG_REF = re.compile(r"(?i)ui_secret|nx_term_ui|permissions\.json|approvals\.json|audit\.jsonl")


def check_command(command: str, cwd: str | os.PathLike | None = None) -> tuple[bool, str]:
    if _TERM_CFG_REF.search(str(command or "")):
        return False, "TERMINAL_KONFIG_GESPERRT (Geheimnis/Rechte/Freigaben/Audit nur durch Eddie)"
    """(ok, grund). Sperren: C:-Pfade/Arbeitsordner ausserhalb G:, destruktive Befehle."""
    cmd = str(command or "")
    if not cmd.strip():
        return False, "LEER"
    if cwd is not None:
        try:
            p = Path(cwd).resolve()
        except Exception:
            return False, "CWD_UNGUELTIG"
        if not str(p).lower().startswith("g:"):
            return False, "CWD_NICHT_G (nur G: erlaubt)"
    if _C_PATH.search(cmd):
        return False, "C_DRIVE_BLOCKED (C: bleibt tabu)"
    from nexus.integrations import github_tool  # 20261102
    gh_block, gh_why = github_tool.hard_block(cmd)
    if gh_block:
        return False, gh_why
    for rx, why in _DESTRUCTIVE:
        if rx.search(cmd):
            return False, "DESTRUKTIV_BLOCKIERT: " + why
    return True, "OK"


def _rotate() -> None:
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_BYTES:
            bak = LOG_FILE.with_suffix(".jsonl.1")
            if bak.exists():
                bak.unlink()
            LOG_FILE.rename(bak)
    except Exception:
        pass


def _write(ev: dict) -> dict:
    ev = {"id": uuid.uuid4().hex[:12], "ts": datetime.now().isoformat(timespec="milliseconds"), **ev}
    for k in ("cmd", "text", "cwd", "reason"):
        if k in ev and ev[k] is not None:
            v = mask(ev[k])
            ev[k] = v if len(v) <= 8000 else v[:8000] + " …[gekuerzt]"
    line = json.dumps(ev, ensure_ascii=False) + "\n"
    try:
        with _LOCK:
            TERM_DIR.mkdir(parents=True, exist_ok=True)
            _rotate()
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
    return ev


def _agent(a) -> str:
    a = str(a or "NEXUS")
    al = a.lower().replace(" ", "-").replace("_", "-")
    if al in ("gpt", "nexusgpt", "nexus-gpt") or al.startswith("nexus-gpt"):
        return "NEXUS-GPT"
    for x in AGENTS:
        if al.startswith(x.lower()):
            return x
    return a[:24]


def record(agent: str, command, *, cwd=None, rc=None, stdout="", stderr="", sec=None, kind="extern", ok=None) -> str:
    """Protokolliert einen bereits ausgefuehrten Lauf. Wirft nie."""
    try:
        run = uuid.uuid4().hex[:10]
        ag = _agent(agent)
        cmd = command if isinstance(command, str) else " ".join(str(x) for x in (command or []))
        _write({"run": run, "agent": ag, "type": "start", "cmd": cmd, "cwd": str(cwd or ROOT), "kind": kind})
        for name, txt in (("out", stdout), ("err", stderr)):
            if txt:
                t = txt if isinstance(txt, str) else str(txt)
                _write({"run": run, "agent": ag, "type": name, "text": t[-6000:]})
        _write({"run": run, "agent": ag, "type": "end", "rc": rc, "sec": sec,
                "ok": (rc == 0) if ok is None else bool(ok)})
        return run
    except Exception:
        return ""


def _argv(command: str, shell: str) -> list[str]:
    sh = (shell or "powershell").lower()
    if sh in ("python", "py"):
        return [sys.executable, "-B", "-c", command]
    if sh == "cmd":
        return ["cmd.exe", "/d", "/c", command] if os.name == "nt" else ["sh", "-c", command]
    if os.name != "nt":
        return ["sh", "-c", command]
    if sh in ("pwsh", "auto", "powershell7", "ps7") and pwsh_path():
        return [pwsh_path(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]


def run(command: str, *, agent: str = "NEXUS", cwd=None, shell: str = "pwsh",
        timeout: int | None = None, wait: bool = True, allow_empty: bool = False, argv: list | None = None) -> dict:
    """Fuehrt einen Befehl aus und streamt ihn ins Terminal. wait=False -> laeuft im Hintergrund."""
    ag = _agent(agent)
    work = Path(cwd) if cwd else ROOT
    to = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    ok, why = check_command(command, work)
    run_id = uuid.uuid4().hex[:10]
    if not ok:
        _write({"run": run_id, "agent": ag, "type": "blocked", "cmd": command, "cwd": str(work), "reason": why, "shell": shell})
        return {"success": False, "status": "BLOCKED", "error": why, "run": run_id}
    if not work.is_dir():
        _write({"run": run_id, "agent": ag, "type": "blocked", "cmd": command, "cwd": str(work), "reason": "CWD_FEHLT"})
        return {"success": False, "status": "BLOCKED", "error": "CWD_FEHLT", "run": run_id}
    _write({"run": run_id, "agent": ag, "type": "start", "cmd": command, "cwd": str(work), "shell": shell, "timeout": to})
    _ACTIVE[run_id] = {"run": run_id, "agent": ag, "cmd": mask(command)[:300], "shell": shell, "ts": datetime.now().isoformat(timespec="seconds")}
    res: dict = {"run": run_id}

    def _go():
        t0 = time.time()
        out_buf: list[str] = []
        err_buf: list[str] = []
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        try:
            p = subprocess.Popen(list(argv) if argv else _argv(command, shell), cwd=str(work), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 encoding="utf-8", errors="replace", env=env, creationflags=flags)
        except Exception as e:
            _write({"run": run_id, "agent": ag, "type": "end", "rc": None, "sec": 0, "ok": False, "text": f"Startfehler: {e}"})
            res.update(success=False, status="ERROR", error=str(e))
            _finish(res, ag, command, allow_empty)
            return
        _RUNS[run_id] = p
        res["pid"] = p.pid

        def pump(stream, typ, buf):
            for line in iter(stream.readline, ""):
                buf.append(line)
                _write({"run": run_id, "agent": ag, "type": typ, "text": line.rstrip("\r\n")})
            stream.close()

        th = [threading.Thread(target=pump, args=(p.stdout, "out", out_buf), daemon=True),
              threading.Thread(target=pump, args=(p.stderr, "err", err_buf), daemon=True)]
        for t in th:
            t.start()
        timed_out = False
        try:
            p.wait(timeout=to)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=15)
                else:
                    p.kill()
            except Exception:
                p.kill()
            p.wait(timeout=15)
        for t in th:
            t.join(timeout=5)
        _RUNS.pop(run_id, None)
        sec = round(time.time() - t0, 2)
        rc = p.returncode
        _write({"run": run_id, "agent": ag, "type": "end", "rc": rc, "sec": sec, "ok": rc == 0 and not timed_out,
                **({"text": f"TIMEOUT nach {to}s, Prozess beendet"} if timed_out else {})})
        cancelled = run_id in _CANCELLED
        res.update(success=rc == 0 and not timed_out and not cancelled,
                   status="CANCELLED" if cancelled else ("TIMEOUT" if timed_out else ("OK" if rc == 0 else "FAILED")),
                   returncode=rc, stdout=mask("".join(out_buf))[-20000:], stderr=mask("".join(err_buf))[-8000:], sec=sec)
        _finish(res, ag, command, allow_empty)

    if wait:
        _go()
        return res
    threading.Thread(target=_go, name="nexus-terminal-" + run_id, daemon=True).start()
    return {"success": True, "status": "STARTED", "run": run_id}


def history(limit: int = 300, agent: str | None = None, run_id: str | None = None) -> list[dict]:
    limit = max(1, min(int(limit or 300), 5000))
    if not LOG_FILE.exists():
        return []
    try:
        with LOG_FILE.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 3_000_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return []
    out = []
    for ln in reversed(lines):
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if agent and str(ev.get("agent", "")).lower() != agent.lower():
            continue
        if run_id and ev.get("run") != run_id:
            continue
        out.append(ev)
        if len(out) >= limit:
            break
    return list(reversed(out))


# ---------------------------------------------------------------- PowerShell 7
_PWSH: list = []


def pwsh_path() -> str | None:
    """pwsh 7: bevorzugt portable unter G:\\tools\\pwsh7, sonst installiertes pwsh (nur lesen)."""
    if _PWSH:
        return _PWSH[0]
    import shutil
    cand = [os.environ.get("NEXUS_PWSH"), r"G:\tools\pwsh7\pwsh.exe", shutil.which("pwsh")]
    for c in cand:
        if c and Path(c).exists():
            _PWSH.append(c)
            return c
    _PWSH.append(None)
    return None


_END = "<<NXEND:"


class PwshSession:
    """Persistente pwsh-7-Sitzung pro Agent (Variablen, Location, Module bleiben erhalten)."""

    def __init__(self, agent: str):
        self.agent = agent
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.started = None
        self.count = 0
        self._q = None

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def _start(self) -> None:
        import queue
        exe = pwsh_path()
        if not exe:
            raise RuntimeError("PWSH7_FEHLT")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        env = dict(os.environ, PYTHONIOENCODING="utf-8", NEXUS_TERMINAL_AGENT=self.agent)
        self.proc = subprocess.Popen([exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"],
                                     cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     env=env, creationflags=flags, bufsize=1)
        self._q = queue.Queue()

        def pump(p=self.proc, q=self._q):
            for line in iter(p.stdout.readline, ""):
                q.put(line.rstrip("\r\n"))
            q.put(None)
        threading.Thread(target=pump, daemon=True, name="nexus-pwsh-" + self.agent).start()
        self.started = datetime.now().isoformat(timespec="seconds")
        self.count = 0
        self.proc.stdin.write("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; Set-Location -LiteralPath '"
                              + str(ROOT).replace("'", "''") + "'\n")
        self.proc.stdin.flush()

    def stop(self) -> None:
        p = self.proc
        self.proc = None
        if p and p.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=15)
                else:
                    p.kill()
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def execute(self, command: str, run_id: str, to: int, cwd: Path | None) -> dict:
        import base64
        import queue
        with self.lock:
            if not self.alive():
                self._start()
            self.count += 1
            self.current = run_id
            b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
            cd = ("Set-Location -LiteralPath '" + str(cwd).replace("'", "''") + "'; ") if cwd else ""
            mark = _END + run_id
            line = (cd + "$global:LASTEXITCODE=0; $__nxok=$true; try { . { Invoke-Expression ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + b64 + "'))) } 2>&1 | "
                    "ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $__nxok=$false; '[ERR] ' + $_.ToString() } else { $_ | Out-String -Stream -Width 220 } } } "
                    "catch { $__nxok=$false; '[ERR] ' + $_.ToString() }; "
                    "$__nxrc = if ($LASTEXITCODE) { $LASTEXITCODE } elseif ($__nxok) { 0 } else { 1 }; "
                    "if (-not ((Get-Location).Path -like 'G:*')) { Set-Location -LiteralPath '" + str(ROOT).replace("'", "''") + "'; '[NEXUS] Arbeitsordner ausserhalb G: -> zurueck auf Projekt' }; "
                    "'" + mark + ":' + $__nxrc + ':' + (Get-Location).Path + '>>'\n")
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
            out: list[str] = []
            t_end = time.time() + to
            while True:
                left = t_end - time.time()
                if left <= 0:
                    self.stop()
                    return {"timeout": True, "out": out, "rc": None, "cwd": None}
                try:
                    ln = self._q.get(timeout=min(left, 1.0))
                except queue.Empty:
                    continue
                if ln is None:
                    self.proc = None
                    return {"timeout": False, "out": out, "rc": -1, "cwd": None, "died": True}
                if ln.startswith(mark + ":"):
                    rest = ln[len(mark) + 1:].rstrip(">")
                    rc_s, _, loc = rest.partition(":")
                    try:
                        rc = int(rc_s)
                    except ValueError:
                        rc = 1
                    return {"timeout": False, "out": out, "rc": rc, "cwd": loc}
                if not ln.strip():
                    continue
                out.append(ln)
                _write({"run": run_id, "agent": self.agent, "type": "err" if ln.startswith("[ERR] ") else "out", "text": ln})


_SESSIONS: dict[str, PwshSession] = {}
_SLOCK = threading.Lock()


def session(agent: str) -> PwshSession:
    ag = _agent(agent)
    with _SLOCK:
        if ag not in _SESSIONS:
            _SESSIONS[ag] = PwshSession(ag)
        return _SESSIONS[ag]


def sessions() -> list[dict]:
    with _SLOCK:
        return [{"agent": a, "alive": s.alive(), "pid": s.proc.pid if s.alive() else None,
                 "started": s.started, "commands": s.count} for a, s in _SESSIONS.items()]


def reset_session(agent: str) -> dict:
    s = session(agent)
    s.stop()
    _write({"run": uuid.uuid4().hex[:10], "agent": s.agent, "type": "info", "text": "Sitzung zurueckgesetzt"})
    return {"success": True, "agent": s.agent}


def run_session(command: str, *, agent: str = "NEXUS", cwd=None, timeout: int | None = None, wait: bool = True,
                allow_empty: bool = False) -> dict:
    """Befehl in der persistenten pwsh-7-Sitzung des Agenten (Standardweg fuer NEXUS/Hermes/NEXUS GPT)."""
    ag = _agent(agent)
    to = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    work = Path(cwd) if cwd else None
    ok, why = check_command(command, work)
    run_id = uuid.uuid4().hex[:10]
    if not ok:
        _write({"run": run_id, "agent": ag, "type": "blocked", "cmd": command, "cwd": str(work or "Sitzung"), "reason": why, "shell": "pwsh7-session"})
        return {"success": False, "status": "BLOCKED", "error": why, "run": run_id}
    if work is not None and not work.is_dir():
        _write({"run": run_id, "agent": ag, "type": "blocked", "cmd": command, "cwd": str(work), "reason": "CWD_FEHLT"})
        return {"success": False, "status": "BLOCKED", "error": "CWD_FEHLT", "run": run_id}
    if not pwsh_path():
        return run(command, agent=ag, cwd=cwd, shell="powershell", timeout=to, wait=wait, allow_empty=allow_empty)
    _write({"run": run_id, "agent": ag, "type": "start", "cmd": command, "cwd": str(work or "Sitzung"), "shell": "pwsh7-session", "timeout": to})
    _ACTIVE[run_id] = {"run": run_id, "agent": ag, "cmd": mask(command)[:300], "shell": "pwsh7-session", "ts": datetime.now().isoformat(timespec="seconds")}
    res: dict = {"run": run_id}

    def _go():
        t0 = time.time()
        try:
            r = session(ag).execute(command, run_id, to, work)
        except Exception as e:
            _write({"run": run_id, "agent": ag, "type": "end", "rc": None, "sec": 0, "ok": False, "text": f"Startfehler: {e}"})
            res.update(success=False, status="ERROR", error=str(e))
            _finish(res, ag, command, allow_empty)
            return
        sec = round(time.time() - t0, 2)
        rc = r["rc"]
        cancelled = run_id in _CANCELLED
        if cancelled:
            r["timeout"] = False
            extra = {"text": "ABGEBROCHEN, Sitzung neu gestartet"}
        else:
            extra = {"text": f"TIMEOUT nach {to}s, Sitzung neu gestartet"} if r["timeout"] else ({"text": "Sitzung beendet (exit) - Werkzeugaufruf kaputt"} if r.get("died") else {})
        _write({"run": run_id, "agent": ag, "type": "end", "rc": rc, "sec": sec, "ok": rc == 0, "cwd": r.get("cwd"), **extra})
        txt = mask("\n".join(r["out"]))
        st = "CANCELLED" if cancelled else ("TIMEOUT" if r["timeout"] else ("ERROR" if r.get("died") else ("OK" if rc == 0 else "FAILED")))
        res.update(success=st == "OK", status=st, returncode=None if cancelled else rc,
                   stdout=txt[-20000:], sec=sec, cwd=r.get("cwd"), shell="pwsh7-session")
        _finish(res, ag, command, allow_empty)

    if wait:
        _go()
        return res
    threading.Thread(target=_go, name="nexus-terminal-" + run_id, daemon=True).start()
    return {"success": True, "status": "STARTED", "run": run_id}


def _finish(res: dict, agent: str, command: str, allow_empty: bool) -> dict:
    """Erfolgspruefung + Audit am Ende jedes Laufs (auch Hintergrund-Laeufe)."""
    from nexus.terminal import guard
    rid = res.get("run")
    _ACTIVE.pop(rid, None)
    _CANCELLED.discard(rid)
    v = guard.verdict(res, allow_empty=allow_empty)
    res.update(v)
    res["success"] = bool(v["ok"])
    _write({"run": rid, "agent": agent, "type": "verdict", "ok": v["ok"], "verdict": v["verdict"], "reasons": v["reasons"],
            "text": v["verdict"] + ("" if v["ok"] else ": " + ", ".join(v["reasons"]))})
    guard.audit("run_result", agent=agent, run=rid, cmd=command, status=res.get("status"), rc=res.get("returncode"),
                sec=res.get("sec"), verdict=v["verdict"], reasons=v["reasons"])
    return res


def active_runs() -> list[dict]:
    return list(_ACTIVE.values())


def cancel(run_id: str, by: str = "Eddie") -> dict:
    """Bricht einen laufenden Befehl ab (Prozessbaum bzw. die Sitzung des Agenten)."""
    from nexus.terminal import guard
    rid = str(run_id or "")
    info = _ACTIVE.get(rid)
    p = _RUNS.get(rid)
    if not info and not p:
        guard.audit("cancel_unknown", run=rid, by=by)
        return {"success": False, "error": "RUN_NICHT_AKTIV", "run": rid}
    _CANCELLED.add(rid)
    try:
        if p is not None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=15)
            else:
                p.kill()
        else:
            with _SLOCK:
                s = next((x for x in _SESSIONS.values() if getattr(x, "current", None) == rid), None)
            if s is not None:
                s.stop()
    except Exception as e:
        guard.audit("cancel_error", run=rid, by=by, error=str(e))
        return {"success": False, "error": str(e), "run": rid}
    _write({"run": rid, "agent": (info or {}).get("agent", "Eddie"), "type": "info", "text": f"Abbruch angefordert von {by}"})
    guard.audit("cancel", run=rid, by=by, agent=(info or {}).get("agent"), cmd=(info or {}).get("cmd"))
    return {"success": True, "run": rid, "cancelled": True}


def execute(command: str, *, agent: str = "NEXUS", shell: str = "session", cwd=None, timeout=None, wait=True,
            approval_id: str | None = None, allow_empty: bool = False, argv: list | None = None,
            force_approval: bool = False) -> dict:
    """Einheitliche API fuer NEXUS, Hermes, NEXUS GPT: shell=session (pwsh7 persistent, Standard) | pwsh | powershell | cmd | python.

    Rechtestufe kommt NUR aus der Terminal-Konfiguration (guard.agent_level), nie aus dem Aufrufer.
    Rueckgabe (strukturiert): ok, verdict ERFOLG|FEHLER, reasons, status, returncode, stdout, stderr, sec, run, level_*.
    """
    from nexus.terminal import guard
    ag = _agent(agent)
    command = str(command or "")
    guard.audit("run_request", agent=ag, cmd=command, shell=shell, cwd=str(cwd or ""), wait=bool(wait))
    if not command.strip():
        res = {"success": False, "status": "ERROR", "error": "LEERER_BEFEHL", "run": None}
        res.update(guard.verdict(res))
        guard.audit("run_rejected", agent=ag, reason="LEERER_BEFEHL")
        return res
    ok, why = check_command(command, Path(cwd) if cwd else ROOT)
    if not ok:
        run_id = uuid.uuid4().hex[:10]
        _write({"run": run_id, "agent": ag, "type": "blocked", "cmd": command, "cwd": str(cwd or ROOT), "reason": why, "shell": shell})
        res = {"success": False, "status": "BLOCKED", "error": why, "run": run_id}
        res.update(guard.verdict(res))
        guard.audit("run_blocked", agent=ag, cmd=command, reason=why)
        return res
    spec = None if argv else {"command": command, "agent": ag, "shell": shell, "cwd": cwd, "timeout": timeout,
                              "allow_empty": allow_empty, "force_approval": force_approval}
    from nexus.safety import gate as _gate  # Phase 2 Schritt 5 (Schalter NEXUS_PHASE2_GATE)
    _authz = _gate.authorize_shell if _gate.enabled() else guard.authorize
    auth = _authz(ag, command, shell, approval_id, force_approval=force_approval, run_spec=spec)
    guard.audit("policy_decision", agent=ag, cmd=command, **{k: auth.get(k) for k in ("status", "level_required", "level_agent", "approval_id")})
    if not auth["allowed"]:
        run_id = uuid.uuid4().hex[:10]
        _write({"run": run_id, "agent": ag, "type": "blocked", "cmd": command, "cwd": str(cwd or ROOT), "shell": shell,
                "reason": auth["status"] + " (" + auth["level_required"] + ")", "approval_id": auth.get("approval_id")})
        res = {"success": False, "run": run_id, **auth}
        res.update(guard.verdict(res))
        return res
    if (shell or "session").lower() in ("session", "pwsh-session", ""):
        res = run_session(command, agent=ag, cwd=cwd, timeout=timeout, wait=wait, allow_empty=allow_empty)
    else:
        res = run(command, agent=ag, cwd=cwd, shell=shell, timeout=timeout, wait=wait, allow_empty=allow_empty, argv=argv)
    res.update({k: auth.get(k) for k in ("level_required", "level_agent")})
    if res.get("status") == "BLOCKED" and "verdict" not in res:
        res.update(guard.verdict(res))
    return res
