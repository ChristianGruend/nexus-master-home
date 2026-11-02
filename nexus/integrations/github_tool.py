"""NEXUS GitHub-Werkzeug (20261102): gh/git ueber das bestehende NEXUS-Terminal (guard + service).

Kein paralleles System: jede Ausfuehrung laeuft ueber nexus.terminal.service.execute
(C:-Sperre, Destruktiv-Sperre, Rechtestufe, approval_id, Audit, Maskierung).
Hier nur: Stufen-Klassifizierung fuer gh/git + harte Token-Sperre + gh-Pfad.

READ_ONLY : gh repo list/view, issue list/view, pr list/view/diff/checks, search, api GET,
            git status/log/diff/show/fetch, git/gh clone nach G:\\meine-ki\\repos
SAFE_WRITE: issue create/comment/edit/close, pr create/comment/edit, branch anlegen,
            git add/commit/pull, git push auf Nicht-Standard-Branch, api POST auf issues/comments/pulls
PRIVILEGED: push auf main/master/Standard-Branch oder ohne Ziel, force/delete/mirror,
            repo delete/rename/edit/archive/create, pr merge, secret/variable/ruleset,
            workflow/run-Aktionen, collaborators, sonstige api-Schreibzugriffe, unbekannte gh-Befehle
GESPERRT  : gh auth token / --show-token / auth login/logout/refresh/setup-git, git credential.
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

GH_DIR = Path(r"G:\meine-ki\tools\gh\app\bin")
GH_EXE = GH_DIR / "gh.exe"
GH_CONFIG_DIR = r"G:\meine-ki\tools\gh\config"
REPOS_DIR = Path(r"G:\meine-ki\repos")
SANDBOX_REPOS = Path(r"G:\meine-ki\nexus-ai\workspace\sandbox\repos")  # 20261102 Hermes-Klonziel
GH_SUBCMDS = {"repo", "search", "issue", "pr", "api", "release", "gist", "label", "workflow", "run", "browse", "status", "auth", "cache"}
DEFAULT_BRANCHES = {"main", "master", "head"}

R, W, P = "READ_ONLY", "SAFE_WRITE", "PRIVILEGED"


def ensure_env() -> None:
    """gh fuer diesen Prozess (und vererbte Terminal-Sitzungen) auffindbar machen; Konfig auf G:."""
    p = os.environ.get("PATH", "")
    if str(GH_DIR).lower() not in p.lower():
        os.environ["PATH"] = p.rstrip(";") + ";" + str(GH_DIR)
    os.environ.setdefault("GH_CONFIG_DIR", GH_CONFIG_DIR)
    os.environ.setdefault("GH_PROMPT_DISABLED", "1")
    os.environ.setdefault("GH_NO_UPDATE_NOTIFIER", "1")


def _tokens(part: str) -> list[str]:
    s = part.strip()
    if s.startswith("&"):
        s = s[1:].strip()
    try:
        t = shlex.split(s, posix=False)
    except ValueError:
        t = s.split()
    t = [x.strip("\"'") for x in t]
    if t:
        head = t[0].replace("/", "\\").lower()
        if head.endswith("\\gh.exe") or head.endswith("\\gh") or head == "gh.exe":
            t[0] = "gh"
        elif head.endswith("\\git.exe") or head == "git.exe":
            t[0] = "git"
    return t


def _parts(cmd: str) -> list[str]:
    return [p for p in re.split(r"[;\n|]|&&|\|\|", str(cmd or "")) if p.strip()]


_BLOCK_RE = re.compile(r"(?i)\bgh(\.exe)?[\"']?\s+auth\s+(token|login|logout|refresh|setup-git|switch)\b|"
                       r"--show-token\b|\bgh(\.exe)?[\"']?\s+auth\s+status\b[^;|\n]*\s-t\b|"
                       r"\bgit(\.exe)?[\"']?\s+credential\b|\bgit(\.exe)?[\"']?\s+config\b[^;|\n]*credential|"
                       r"GH_TOKEN|GITHUB_TOKEN|hosts\.yml")


def hard_block(cmd: str) -> tuple[bool, str]:
    """(gesperrt, grund) - Token/Anmeldung nie durch Agenten."""
    if _BLOCK_RE.search(str(cmd or "")):
        return True, "GITHUB_TOKEN_GESPERRT (Token/Anmeldung nur durch Eddie selbst)"
    return False, ""


def _flag(t: list[str], *names: str) -> str | None:
    for i, x in enumerate(t):
        for n in names:
            if x == n and i + 1 < len(t):
                return t[i + 1]
            if x.startswith(n + "="):
                return x.split("=", 1)[1]
    return None


def _in_repos(path: str) -> bool:
    q = Path(path)
    for base in (REPOS_DIR, SANDBOX_REPOS):
        try:
            (q if q.is_absolute() else base / q).resolve().relative_to(base.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def normalize(command: str) -> str:
    """20261102: Modell laesst 'gh' oft weg ('clone https://github.com/x', 'search repos x') -> ergaenzen."""
    c = str(command or "").strip()
    first = c.split(None, 1)[0].lower() if c else ""
    if first in GH_SUBCMDS:
        return "gh " + c
    if first == "clone":
        rest = c.split(None, 1)[1] if " " in c else ""
        rest = rest.replace("https://github.com/", "").rstrip("/")
        return "gh repo clone " + rest
    return c


def _gh(t: list[str]) -> tuple[str, str]:
    a = [x.lower() for x in t[1:]]
    if not a:
        return R, "gh Hilfe"
    g, sub = a[0], (a[1] if len(a) > 1 else "")
    if g in ("--version", "version", "help", "--help"):
        return R, "gh Info"
    if g == "auth" and sub == "status":
        return R, "gh auth status"
    if g == "repo":
        if sub in ("list", "view", ""):
            return R, "gh repo lesen"
        if sub == "clone":
            pos = [x for x in t[3:] if not x.startswith("-")]
            if len(pos) < 2 or _in_repos(pos[1]):
                return R, "gh repo clone nach G:\\meine-ki\\repos"
            return W, "gh repo clone ausserhalb repos"
        if sub == "fork":
            return W, "gh repo fork"
        return P, "gh repo " + sub + " (Repo-Einstellungen/Loeschen)"
    if g in ("issue", "pr"):
        if sub in ("list", "view", "status", "diff", "checks", ""):
            return R, f"gh {g} lesen"
        if g == "pr" and sub == "merge":
            return P, "gh pr merge"
        if sub in ("create", "comment", "edit", "close", "reopen", "checkout", "ready", "review", "develop", "lock", "unlock"):
            return W, f"gh {g} {sub}"
        return P, f"gh {g} {sub} (unbekannt)"
    if g == "search":
        return R, "gh search"
    if g in ("release", "label", "gist", "browse", "status", "workflow", "run", "cache"):
        if sub in ("list", "view", "status", "") and g != "browse":
            return R, f"gh {g} lesen"
        return P, f"gh {g} {sub}"
    if g == "api":
        method = (_flag(t, "-X", "--method") or "").upper()
        has_body = any(x in ("-f", "-F", "--field", "--raw-field", "--input") or x.startswith(("-f=", "-F="))
                       for x in t[2:])
        if not method:
            method = "POST" if has_body else "GET"
        ep = next((x for x in t[2:] if not x.startswith("-") and ("/" in x or x == "graphql")), "")
        if method == "GET" and ep != "graphql":
            return R, "gh api GET"
        if method == "POST" and re.search(r"/(issues|pulls)(/\d+)?(/comments)?$|/issues/\d+/labels$", ep):
            return W, "gh api POST Issue/PR/Kommentar"
        return P, f"gh api {method} {ep}"
    return P, "gh " + g + " (nicht freigegeben)"


def _git(t: list[str]) -> tuple[str, str] | None:
    a = t[1:]
    # globale Optionen wie -C <pfad> ueberspringen
    while a and a[0] in ("-C", "-c"):
        a = a[2:]
    if not a:
        return R, "git Hilfe"
    sub, rest = a[0].lower(), a[1:]
    low = [x.lower() for x in rest]
    if sub in ("status", "log", "diff", "show", "fetch", "rev-parse", "ls-files", "blame", "remote", "--version", "version"):
        if sub == "remote" and low and low[0] not in ("-v", "show", "get-url"):
            return P, "git remote aendern"
        return R, "git lesen"
    if sub == "clone":
        pos = [x for x in rest if not x.startswith("-")]
        if len(pos) < 2 or _in_repos(pos[1]):
            return R, "git clone nach G:\\meine-ki\\repos"
        return W, "git clone ausserhalb repos"
    if sub == "branch":
        if any(x in ("-d", "-D", "--delete", "-m", "-M", "--move") for x in low):
            return P, "git branch loeschen/umbenennen"
        return (R, "git branch lesen") if not [x for x in rest if not x.startswith("-")] else (W, "git branch anlegen")
    if sub in ("checkout", "switch", "add", "commit", "pull", "stash", "restore"):
        return W, "git " + sub
    if sub == "push":
        if any(x in ("-f", "--force", "--force-with-lease", "--delete", "-d", "--mirror", "--all", "--prune") or x.startswith("--force")
               or x.startswith("+") for x in low):
            return P, "git push force/delete/mirror"
        pos = [x for x in rest if not x.startswith("-")]
        if len(pos) < 2:
            return P, "git push ohne Ziel-Branch (koennte Standard-Branch sein)"
        for ref in pos[1:]:
            if ref.startswith(":"):
                return P, "git push loescht Remote-Branch"
            dst = ref.split(":")[-1].lower().replace("refs/heads/", "")
            if dst in DEFAULT_BRANCHES:
                return P, "git push auf Standard-Branch " + dst
        return W, "git push auf Nicht-Standard-Branch"
    if sub in ("merge", "rebase", "reset", "cherry-pick", "revert", "tag", "clean", "config", "filter-branch", "gc", "worktree"):
        return P, "git " + sub
    return None


def classify(command: str) -> tuple[str, str] | None:
    """Stufe fuer gh/git-Befehle; None wenn kein gh/git-Befehl (dann normale Guard-Regeln)."""
    parts = _parts(command)
    if not parts:
        return None
    results = []
    for p in parts:
        t = _tokens(p)
        if not t or t[0] not in ("gh", "git"):
            return None if not results else None
        r = _gh(t) if t[0] == "gh" else _git(t)
        if r is None:
            return None
        results.append(r)
    order = {R: 0, W: 1, P: 2}
    return max(results, key=lambda r: order[r[0]])


def default_branch(cwd: str | os.PathLike | None) -> str | None:
    """Echter Standard-Branch des geklonten Repos (lokal, ohne Netz)."""
    import subprocess
    try:
        r = subprocess.run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=str(cwd or REPOS_DIR),
                           capture_output=True, text=True, timeout=10)
        v = (r.stdout or "").strip()
        return v.split("/", 1)[1] if "/" in v else None
    except Exception:
        return None


def run(command: str, *, agent: str = "NEXUS", cwd: str | None = None, approval_id: str | None = None,
        timeout: int | None = 180) -> dict:
    """GitHub-Befehl ueber das NEXUS-Terminal ausfuehren (Guard, Freigabe, Audit, Maskierung inklusive)."""
    ensure_env()
    cmd = normalize(command)
    blocked, why = hard_block(cmd)
    if blocked:
        return {"success": False, "status": "BLOCKED", "error": why}
    cls = classify(cmd)
    if cls is None:
        return {"success": False, "status": "BLOCKED", "error": "NUR_GH_GIT: Befehl muss mit gh oder git beginnen, z. B. 'gh search repos NAME', 'gh repo view OWNER/REPO', 'gh repo clone OWNER/REPO " + str(SANDBOX_REPOS) + "\\REPO'"}
    work = Path(cwd) if cwd else REPOS_DIR
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    force = False
    if cls[0] == W and re.search(r"(?i)\bgit\s+push\b", cmd):
        db = default_branch(work)
        if db and re.search(r"(?i)(^|[\s:])" + re.escape(db) + r"(\s|$)", cmd.split("push", 1)[1]):
            force = True  # Push auf echten Standard-Branch -> Freigabe
    if cmd.lower().startswith("gh "):
        cmd = "& '" + str(GH_EXE) + "'" + cmd[2:]
    from nexus.terminal import service
    r = service.execute(cmd, agent=agent, shell="session", cwd=str(work), timeout=timeout,
                        approval_id=approval_id, force_approval=force)
    return {"tool": "github", "level": cls[0], "level_reason": cls[1], **r, "success": bool(r.get("ok"))}


ensure_env()
