"""NEXUS-Terminal Guard (20261031): Rechtestufen, Freigaben, Erfolgspruefung, Audit.

Rechtestufen (vom Terminal per Konfiguration vergeben, NIE von der KI):
  READ_ONLY  - nur lesende Befehle
  SAFE_WRITE - lesen + schreiben, nur auf G: (C: und fremde Laufwerke gesperrt)
  PRIVILEGED - System-/Netz-/Installationsbefehle; jeder einzelne Lauf braucht eine
               approval_id, die Eddie im UI freigibt (einmalig, an den Befehl gebunden).
Konfiguration: workspace/terminal/permissions.json  {"agents": {"NEXUS": "SAFE_WRITE", ...}}
Audit: workspace/terminal/audit.jsonl (jede Anfrage, Entscheidung, Freigabe, Abbruch, Ergebnis).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from nexus.common.paths import ROOT

LEVELS = ("READ_ONLY", "SAFE_WRITE", "PRIVILEGED")
_RANK = {k: i for i, k in enumerate(LEVELS)}
DEFAULT_AGENT_LEVELS = {"NEXUS": "SAFE_WRITE", "Claude": "SAFE_WRITE", "Hermes": "SAFE_WRITE",
                        "NEXUS-GPT": "SAFE_WRITE", "Autobuild": "SAFE_WRITE", "Eddie": "PRIVILEGED",
                        "AETHER": "SAFE_WRITE"}
APPROVAL_TTL_SEC = 15 * 60
_LOCK = threading.Lock()


def term_dir() -> Path:
    from nexus.terminal import service
    return Path(service.TERM_DIR)


def _perm_file() -> Path:
    return term_dir() / "permissions.json"


def _approvals_file() -> Path:
    return term_dir() / "approvals.json"


def audit_file() -> Path:
    return term_dir() / "audit.jsonl"


def agent_levels() -> dict:
    lv = dict(DEFAULT_AGENT_LEVELS)
    fp = _perm_file()
    try:
        if fp.exists():
            data = json.loads(fp.read_text(encoding="utf-8"))
            for k, v in (data.get("agents") or {}).items():
                if v in LEVELS:
                    lv[str(k)] = v
        else:
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps({"_info": "Rechtestufen pro Agent. Nur Eddie aendert diese Datei. READ_ONLY | SAFE_WRITE | PRIVILEGED",
                                      "agents": lv}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return lv


def agent_level(agent: str) -> str:
    return agent_levels().get(agent, "READ_ONLY")


# --- Klassifizierung ---------------------------------------------------------
_PRIV = re.compile(r"(?i)\b(start-process|set-executionpolicy|new-service|set-service|sc(\.exe)?\s+(create|config|delete)|schtasks|"
                   r"register-scheduledtask|unregister-scheduledtask|reg(\.exe)?\s+(add|import)|set-itemproperty\s+[^|;]*hk(lm|cu)|new-itemproperty\s+[^|;]*hk(lm|cu)|"
                   r"install-module|install-package|uninstall-|winget|choco|scoop|msiexec|pip3?\s+install|python\s+-m\s+pip\s+install|npm\s+(i|install)\s+-g|"
                   r"git\s+push|netsh|new-netfirewallrule|set-netfirewall|runas|enable-psremoting|invoke-command\s+-computername|"
                   r"stop-process|taskkill|restart-service|stop-service|start-service|set-acl|icacls|"
                   r"invoke-webrequest|invoke-restmethod|\biwr\b|\birm\b|curl(\.exe)?\s|wget\s|start-bitstransfer|"
                   r"add-mppreference|set-mppreference|new-psdrive|subst\s|mklink|new-item\s+[^|;]*-itemtype\s+(symboliclink|junction))")
_WRITE = re.compile(r"(?i)(>>?|\b(set-content|add-content|out-file|new-item|ni|mkdir|md|copy-item|cp|copy|move-item|mv|move|rename-item|ren|"
                    r"remove-item|rm|del|erase|rd|rmdir|ri|clear-content|set-item|tee-object|expand-archive|compress-archive|git\s+(add|commit|checkout|reset|stash|merge|rebase|apply|restore|switch|pull)|"
                    r"python|py|pytest|pip|node|npm|ollama)\b|\[io\.file\]::write|\.write\(|open\()")
_READ_FIRST = re.compile(r"(?i)^\s*(get-[a-z]+|gci|ls|dir|cat|type|gc|select-string|sls|test-path|resolve-path|measure-object|"
                         r"format-[a-z]+|ft|fl|where-object|where|\?|foreach-object|%|sort-object|sort|select-object|select|group-object|"
                         r"echo|write-output|write-host|\$[a-z_]+|\(|\"|'|[0-9]|git\s+(status|log|diff|show|branch|rev-parse|ls-files|blame)|"
                         r"hostname|whoami|ping|ipconfig|tasklist|findstr|where\.exe|compare-object|convertto-json|convertfrom-json|out-string|"
                         r"\$psversiontable|pwd|cd|set-location|sl|push-location|pop-location)\b")
_OTHER_DRIVE = re.compile(r"(?i)(?<![a-z0-9])([abd-fh-z]):[\\/]")
# POSIX (Linux/macOS): System, Rechte, Pakete, Prozesse, Netz-Tunnel, verschachtelte Shells (Stufe 2.2a)
_PRIV_POSIX = re.compile(r"(?<![\w./-])(sudo|su|doas|pkexec|chmod|chown|chgrp|chattr|setfacl|dd|mkfs(\.\w+)?|fdisk|parted|wipefs|mount|umount|"
                         r"systemctl|service|loginctl|journalctl\s+--vacuum|kill|pkill|killall|shutdown|reboot|poweroff|halt|"
                         r"apt(-get)?|dpkg|pacman|yay|paru|dnf|yum|zypper|snap|flatpak|brew|pipx|cargo\s+install|"
                         r"crontab|useradd|usermod|userdel|passwd|visudo|iptables|nft|ufw|firewall-cmd|"
                         r"ssh|scp|sftp|rsync|nc|ncat|netcat|socat|telnet|ftp|"
                         r"(ba|z|da|k|c|tc|fi)?sh\s+-c|eval|exec|source|xargs|nohup|setsid|disown|docker|podman|kubectl)(?![\w./-])")
# Geheime Dateien/Orte: Lesen ist nie "nur lesen" (Stufe 2.2a)
_SECRET_PATH = re.compile(r"(?i)(\.ssh/|\.gnupg/|\.aws/|\.config/gh/|\.netrc|\.git-credentials|\.hermes/\.env|\.kimi-code|"
                          r"workspace[\\/]secrets|(^|[\s'\"\\/=])\.env\b|\bid_(rsa|ed25519|ecdsa)|/etc/shadow|\.token\b|api_token\.txt|ui_secret\.txt)")


# --- 2026-11-02 16:34 Eddie: Loeschen ausserhalb workspace\sandbox nur mit approval_id (alle Agenten) ---
_DELETE_RX = re.compile(r"(?i)(?<![\w-])(remove-item|rm|del|erase|rd|rmdir|ri|clear-content|clc|clear-item|cli|clear-recyclebin|"
                        r"move-item|mv|move|mi|robocopy|git\s+(clean|rm|reset\s+--hard|checkout\s+--|restore)|\[io\.(file|directory)\]::(delete|move)|"
                        r"\.delete\(|os\.remove|os\.unlink|shutil\.rmtree|unlink\(|rmtree)(?![\w-])")
_SANDBOX_ESCAPE_RX = re.compile(r"(?i)([a-z]:|\.\.|(^|[\s'\"(=,])[\\/~]|~|\$|%[a-z_]+%|`|\b(set-location|cd|chdir|sl|push-location|pushd|pop-location|popd|"
                                r"invoke-expression|iex|start-process|cmd|powershell|pwsh|python|py)\b)")


def sandbox_dir() -> Path:
    return (Path(__file__).resolve().parents[2] / "workspace" / "sandbox").resolve()


def _in_sandbox_cwd(cwd) -> bool:
    if not cwd:
        return False
    try:
        p = Path(str(cwd))
        rp = p.resolve()
        sb = sandbox_dir()
        if not (rp == sb or rp.is_relative_to(sb)):
            return False
        cur = p.absolute()   # kein Symlink/Junction auf dem Weg
        while True:
            if cur.is_symlink() or (os.path.isdir(cur) and os.path.realpath(cur).lower() != str(cur.resolve()).lower()):
                return False
            if cur.resolve() == sb or cur.parent == cur:
                break
            cur = cur.parent
        return True
    except Exception:
        return False


def delete_outside_sandbox(command: str, cwd=None) -> bool:
    """True = loeschender/verschiebender Befehl, der NICHT sicher nur in der Sandbox wirkt -> PRIVILEGED."""
    cmd = str(command or "")
    if not _DELETE_RX.search(cmd):
        return False
    return not (_in_sandbox_cwd(cwd) and not _SANDBOX_ESCAPE_RX.search(cmd))


def required_level(command: str) -> tuple[str, str]:
    cmd = str(command or "")
    if _OTHER_DRIVE.search(cmd):
        return "PRIVILEGED", "Laufwerk ausserhalb G:"
    from nexus.integrations import github_tool  # GitHub-Stufen (20261102)
    gh_lvl = github_tool.classify(cmd)
    if gh_lvl:
        return gh_lvl
    if _PRIV.search(cmd):
        return "PRIVILEGED", "System/Netz/Installation/Prozesse"
    if _PRIV_POSIX.search(cmd):
        return "PRIVILEGED", "System/Rechte/Pakete/Prozesse/Netz (POSIX)"
    if _SECRET_PATH.search(cmd):
        return "PRIVILEGED", "Zugriff auf geheime Dateien"
    parts = [p for p in re.split(r"[;\n|]|&&|\|\|", cmd) if p.strip()]
    if not _WRITE.search(cmd) and parts and all(_READ_FIRST.search(p) for p in parts):
        return "READ_ONLY", "nur lesen"
    return "SAFE_WRITE", "schreibt/fuehrt aus (nur G:)"


def required_level_shell(command: str, shell: str, cwd=None) -> tuple[str, str]:
    sh = (shell or "session").lower()
    lvl, why = required_level(command)
    if lvl != "PRIVILEGED" and delete_outside_sandbox(command, cwd):
        return "PRIVILEGED", "Loeschen/Verschieben ausserhalb workspace\\sandbox"
    if sh in ("python", "py") and lvl == "READ_ONLY":
        return "SAFE_WRITE", "Python-Code"
    return lvl, why


# --- Freigaben ---------------------------------------------------------------
def _cmd_hash(agent: str, command: str) -> str:
    return hashlib.sha256((agent + "\x00" + command).encode("utf-8")).hexdigest()[:24]


def _load_approvals() -> dict:
    try:
        return json.loads(_approvals_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_approvals(d: dict) -> None:
    fp = _approvals_file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    d = {k: v for k, v in d.items() if now - v.get("created", now) < 24 * 3600}
    fp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


_AUTORUN: dict = {}  # approval_id -> Lauf-Spezifikation (nur im Speicher, nie auf Platte/Log)


def request_approval(agent: str, command: str, reason: str, run_spec: dict | None = None) -> str:
    from nexus.terminal.service import mask
    aid = _request_approval(agent, command, reason, mask)
    if run_spec:
        _AUTORUN[aid] = dict(run_spec)
    return aid


def _request_approval(agent: str, command: str, reason: str, mask) -> str:
    with _LOCK:
        d = _load_approvals()
        h = _cmd_hash(agent, command)
        for k, v in d.items():
            if v.get("hash") == h and v.get("state") in ("pending", "approved") and time.time() - v["created"] < APPROVAL_TTL_SEC:
                return k
        aid = "apr_" + uuid.uuid4().hex[:12]
        d[aid] = {"agent": agent, "cmd": mask(command)[:2000], "hash": h, "reason": reason, "state": "pending",
                  "created": time.time(), "ts": datetime.now().isoformat(timespec="seconds")}
        _save_approvals(d)
    audit("approval_requested", agent=agent, cmd=command, approval_id=aid, reason=reason)
    return aid


def _expires(v: dict) -> float:
    return float(v.get("expires") or (v.get("created", 0) + APPROVAL_TTL_SEC))


def pending_approvals() -> list[dict]:
    d = _load_approvals()
    now = time.time()
    return [{"approval_id": k, **{x: v.get(x) for x in ("agent", "cmd", "reason", "kind", "state", "ts")}}
            for k, v in d.items() if v.get("state") == "pending" and now < _expires(v)]


def decide(approval_id: str, approve: bool, by: str = "Eddie-UI") -> dict:
    with _LOCK:
        d = _load_approvals()
        v = d.get(approval_id)
        if not v or v.get("state") != "pending":
            return {"success": False, "error": "APPROVAL_UNKNOWN_OR_DONE"}
        if time.time() > _expires(v):
            v["state"] = "expired"
            _save_approvals(d)
            return {"success": False, "error": "APPROVAL_EXPIRED"}
        v["state"] = "approved" if approve else "denied"
        v["decided_by"] = by
        v["decided"] = time.time()
        _save_approvals(d)
    audit("approval_" + ("granted" if approve else "denied"), agent=v["agent"], cmd=v["cmd"], approval_id=approval_id, by=by)
    out = {"success": True, "approval_id": approval_id, "state": v["state"], "agent": v["agent"], "cmd": v["cmd"]}
    spec = _AUTORUN.pop(approval_id, None)
    if approve and spec:
        # freigegebener Werkzeugaufruf (z. B. NEXUS-Chat) laeuft jetzt live im Terminal
        from nexus.terminal import service
        r = service.execute(spec.pop("command"), approval_id=approval_id, wait=False, **spec)
        out["started_run"] = r.get("run")
        out["run_status"] = r.get("status")
        audit("approval_autorun", agent=v["agent"], approval_id=approval_id, run=r.get("run"), status=r.get("status"))
    return out


def consume(approval_id: str | None, agent: str, command: str) -> bool:
    if not approval_id:
        return False
    with _LOCK:
        d = _load_approvals()
        v = d.get(str(approval_id))
        if not v or v.get("state") != "approved" or v.get("hash") != _cmd_hash(agent, command):
            return False
        if time.time() - v["created"] > APPROVAL_TTL_SEC:
            return False
        v["state"] = "used"
        v["used"] = time.time()
        _save_approvals(d)
    return True


# --- Phase 1 (WP2): Freigaben fuer Nicht-Shell-Werkzeuge (Aether) -------------
def _action_hash(agent: str, action: str, params: dict) -> str:
    raw = json.dumps({"agent": agent, "action": action, "params": params},
                     sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def request_action_approval(agent: str, action: str, params: dict, reason: str = "",
                            ttl_s: int | None = None, description: str | None = None) -> str:
    """Erzeugt NUR eine wartende Anfrage. Freigabe ausschliesslich ueber die bestehende UI (decide)."""
    from nexus.terminal.service import mask
    ttl = float(ttl_s if ttl_s is not None else APPROVAL_TTL_SEC)
    h = _action_hash(agent, action, params)
    with _LOCK:
        d = _load_approvals()
        for k, v in d.items():
            if (v.get("kind") == "action" and v.get("agent") == agent and v.get("action") == action
                    and v.get("hash") == h and v.get("state") in ("pending", "approved")
                    and time.time() < float(v.get("expires") or (v.get("created", 0) + ttl))):
                return k
        aid = "apr_" + uuid.uuid4().hex[:12]
        desc = description or f"{action} {json.dumps(params, ensure_ascii=False, default=str)[:400]}"
        d[aid] = {"kind": "action", "agent": agent, "action": action, "hash": h,
                  "params": params, "cmd": mask(desc)[:2000], "reason": reason,
                  "state": "pending", "created": time.time(), "expires": time.time() + ttl,
                  "ts": datetime.now().isoformat(timespec="seconds")}
        _save_approvals(d)
    audit("action_approval_requested", agent=agent, tool_action=action,
          approval_id=aid, reason=reason)
    return aid


def consume_action_approval(approval_id: str | None, agent: str, action: str, params: dict) -> bool:
    if not approval_id:
        return False
    with _LOCK:
        d = _load_approvals()
        v = d.get(str(approval_id))
        if not v or v.get("kind") != "action":
            return False
        if v.get("agent") != agent or v.get("action") != action:
            return False
        if v.get("state") != "approved":
            return False
        if v.get("hash") != _action_hash(agent, action, params):
            return False
        if time.time() > float(v.get("expires") or 0):
            return False
        v["state"] = "used"
        v["used"] = time.time()
        _save_approvals(d)
    audit("action_approval_consumed", agent=agent, tool_action=action, approval_id=approval_id)
    return True


def authorize(agent: str, command: str, shell: str = "session", approval_id: str | None = None,
              force_approval: bool = False, run_spec: dict | None = None) -> dict:
    """Entscheidet anhand der konfigurierten Stufe. Rueckgabe: {allowed, level_required, level_agent, status, ...}."""
    need, why = required_level_shell(command, shell, (run_spec or {}).get("cwd"))
    have = agent_level(agent)
    base = {"level_required": need, "level_agent": have, "level_reason": why}
    if need == "PRIVILEGED" or force_approval:
        if consume(approval_id, agent, command):
            return {"allowed": True, "status": "APPROVED", "approval_id": approval_id, **base}
        if force_approval and need != "PRIVILEGED":
            why = "Chat-Werkzeugaufruf: Eddie gibt jeden Befehl frei (" + why + ")"
        aid = request_approval(agent, command, why, run_spec)
        return {"allowed": False, "status": "APPROVAL_REQUIRED", "approval_id": aid,
                "error": "PRIVILEGED: Freigabe durch Eddie im Terminal-UI noetig (approval_id " + aid + ")", **base}
    if _RANK[have] < _RANK[need]:
        return {"allowed": False, "status": "LEVEL_DENIED",
                "error": f"Rechtestufe {have} reicht nicht (braucht {need}: {why})", **base}
    return {"allowed": True, "status": "OK", **base}


# --- Erfolgspruefung ---------------------------------------------------------
_ERR_HINT = re.compile(r"(?im)^(\[ERR\]|Traceback \(most recent call last\)|.*\b(Exception|Error):|FAILED\b|fatal:)")


def verdict(res: dict, *, allow_empty: bool = False) -> dict:
    """Strenge Pruefung: rc!=0, Timeout, Abbruch, leere/fehlende Ausgabe, Fehlertexte -> FEHLER."""
    reasons = []
    st = res.get("status")
    rc = res.get("returncode")
    out = (res.get("stdout") or "").strip()
    err = (res.get("stderr") or "").strip()
    if st in ("TIMEOUT", "CANCELLED", "ERROR", "BLOCKED", "APPROVAL_REQUIRED", "LEVEL_DENIED"):
        reasons.append(st)
    if rc is None:
        reasons.append("KEIN_EXIT_CODE")
    elif rc != 0:
        reasons.append(f"EXIT_{rc}")
    if not out and not err and not allow_empty:
        reasons.append("LEERE_AUSGABE")
    if _ERR_HINT.search(out) or (err and _ERR_HINT.search(err)):
        reasons.append("FEHLERTEXT_IN_AUSGABE")
    ok = not reasons
    return {"ok": ok, "verdict": "ERFOLG" if ok else "FEHLER", "reasons": reasons}


# --- Audit -------------------------------------------------------------------
def audit(action: str, **kw) -> None:
    try:
        from nexus.terminal.service import mask
        rec = {"ts": datetime.now().isoformat(timespec="milliseconds"), "action": action}
        for k, v in kw.items():
            if v is None:
                continue
            rec[k] = mask(v)[:4000] if isinstance(v, str) else v
        fp = audit_file()
        fp.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            if fp.exists() and fp.stat().st_size > 5 * 1024 * 1024:
                b = fp.with_suffix(".jsonl.1")
                if b.exists():
                    b.unlink()
                fp.rename(b)
            with fp.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def audit_tail(limit: int = 200) -> list[dict]:
    fp = audit_file()
    if not fp.exists():
        return []
    out = []
    for ln in fp.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(int(limit), 2000)):]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


# --- UI-Geheimnis fuer Freigaben ---------------------------------------------
COOKIE_NAME = "nx_term_ui"


def _secret_file() -> Path:
    return term_dir() / "ui_secret.txt"


def ui_secret() -> str:
    """Zufaelliges lokales Geheimnis (workspace/terminal/ui_secret.txt); nie im Log, nie im Code."""
    fp = _secret_file()
    with _LOCK:
        try:
            v = fp.read_text(encoding="utf-8").strip()
            if len(v) >= 32:
                return v
        except OSError:
            pass
        v = secrets.token_urlsafe(32)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(v, encoding="utf-8")
        return v


def ui_cookie() -> str:
    return f"{COOKIE_NAME}={ui_secret()}; Path=/api/terminal; HttpOnly; SameSite=Strict"


def check_ui_cookie(cookie_header: str | None) -> bool:
    if not cookie_header:
        return False
    got = ""
    for part in str(cookie_header).split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME:
            got = v.strip()
    return bool(got) and hmac.compare_digest(got.encode(), ui_secret().encode())


def chat_shell_mode() -> str:
    """'approval' (Standard): jeder NEXUS-Chat-Befehl braucht Eddies Freigabe; 'level': nur Rechtestufe."""
    try:
        d = json.loads(_perm_file().read_text(encoding="utf-8"))
        m = str(d.get("nexus_chat_shell") or "approval")
        return m if m in ("approval", "level") else "approval"
    except Exception:
        return "approval"
