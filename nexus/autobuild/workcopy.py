"""Arbeitskopie fuer den Autobuild (Stufe 2.4): Aenderungen entstehen nie im Live-Repo.

Pro Zyklus eine Kopie unter shared_dir()/autobuild/<cid> (im Runner sichtbar als /work/autobuild/<cid>).
Das git-Verzeichnis liegt core-only unter WORKSPACE/autobuild/gitdirs/<cid> (nie im Runner-Volume), alle git-Aufrufe
auf die Kopie mit explizitem --git-dir/--work-tree: der Runner kann so keine git-Konfiguration einschmuggeln.
- ROOT mit .git: flacher Klon des HEAD (ohne Hardlinks, Basis = HEAD).
- ROOT ohne .git (Container): Baum kopieren, frisches git-Repo mit Basis-Commit.
Veroeffentlicht wird als Branch im echten Repo (ohne dessen Arbeitsverzeichnis anzufassen) bzw. als Patch-Datei.
Gemergt wird nie automatisch.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from nexus.autobuild.guard import child_env
from nexus.autobuild.scope import violations as _scope_violations
from nexus.common import paths as _paths
from nexus.runner.client import shared_dir, to_runner_cwd

_IDENT = ["-c", "user.name=nexus-autobuild", "-c", "user.email=autobuild@localhost", "-c", "commit.gpgsign=false",
          "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.untrackedCache=false"]
_COPY_IGNORE = {".git", "__pycache__", ".venv", "venv", ".pytest_cache", "logs", ".ruff_cache", ".mypy_cache"}
_EXCLUDE = "__pycache__/\n*.pyc\n.pytest_cache/\n.ruff_cache/\n.mypy_cache/\n"


class WorkCopyError(RuntimeError):
    pass


def _env() -> dict:
    env = child_env()
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CEILING_DIRECTORIES=str(shared_dir()))
    return env


def _git(args: list[str], cwd, check: bool = True) -> subprocess.CompletedProcess:
    """git im vertrauenswuerdigen Verzeichnis (ROOT, tmp ausserhalb der Kopie)."""
    p = subprocess.run(["git", *_IDENT, *args], cwd=str(cwd), env=_env(), capture_output=True, text=True,
                       timeout=120, stdin=subprocess.DEVNULL)
    if check and p.returncode != 0:
        raise WorkCopyError(f"git {' '.join(args[:2])}: {(p.stderr or p.stdout).strip()[:300]}")
    return p


def _cgit(gitdir: Path, work: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """git auf der Kopie: immer explizit --git-dir/--work-tree (core-only gitdir), nie Auto-Discovery."""
    return _git([f"--git-dir={gitdir}", f"--work-tree={work}", *args], work.parent, check)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._/-]+", "-", name).strip("-/") or "item"


class WorkCopy:
    def __init__(self, cid: str, dst: Path, root: Path, base: str, git_root: bool, gitdir: Path) -> None:
        self.cid = cid
        self.dst = dst
        self.root = root
        self.base = base
        self.git_root = git_root
        self.gitdir = gitdir

    # ---------- Anlegen ----------
    @classmethod
    def create(cls, cid: str, root: Path | None = None) -> "WorkCopy":
        root = Path(root or _paths.ROOT)
        dst = shared_dir() / "autobuild" / cid
        gitdir = _paths.WORKSPACE / "autobuild" / "gitdirs" / cid  # core-only, nie im Runner-Volume
        for d in (dst, gitdir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        gitdir.parent.mkdir(parents=True, exist_ok=True)
        try:
            if (root / ".git").exists():
                base = _git(["rev-parse", "HEAD"], root).stdout.strip()
                url = f"file://{root.as_posix()}"
                _git(["clone", "--quiet", "--depth", "1", "--no-local", f"--separate-git-dir={gitdir}", url, str(dst)], dst.parent)
                (dst / ".git").unlink(missing_ok=True)  # der Runner braucht kein git
                head = _cgit(gitdir, dst, ["rev-parse", "HEAD"]).stdout.strip()
                if head != base:  # Klon zeigt nicht auf denselben Commit -> Basis gezielt holen
                    _cgit(gitdir, dst, ["fetch", "--quiet", "--depth", "1", url, base])
                    _cgit(gitdir, dst, ["checkout", "--quiet", "--detach", base])
                git_root = True
            else:
                base = cls._copy_tree(root, dst, gitdir)
                git_root = False
        except Exception:
            shutil.rmtree(dst, ignore_errors=True)
            shutil.rmtree(gitdir, ignore_errors=True)
            raise
        wc = cls(cid, dst, root, base, git_root, gitdir)
        excl = gitdir / "info" / "exclude"
        excl.parent.mkdir(parents=True, exist_ok=True)
        with open(excl, "a", encoding="utf-8") as fh:
            fh.write(_EXCLUDE)
        return wc

    @staticmethod
    def _copy_tree(root: Path, dst: Path, gitdir: Path) -> str:
        def ignore(d, names):
            skip = {n for n in names if n in _COPY_IGNORE or n.endswith(".pyc")}
            if Path(d).resolve() == root.resolve():
                skip |= {n for n in names if n == "workspace"}
            return skip

        shutil.copytree(root, dst, ignore=ignore, symlinks=True)
        _git([f"--git-dir={gitdir}", f"--work-tree={dst}", "init", "-q"], dst.parent)
        _cgit(gitdir, dst, ["add", "-A"])
        _cgit(gitdir, dst, ["commit", "-qm", "basis", "--allow-empty"])
        return _cgit(gitdir, dst, ["rev-parse", "HEAD"]).stdout.strip()

    # ---------- Zugriff ----------
    @property
    def runner_cwd(self) -> str | None:
        return to_runner_cwd(self.dst)

    def path(self, rel: str) -> Path:
        p = (self.dst / rel).resolve()
        if p != self.dst.resolve() and not p.is_relative_to(self.dst.resolve()):
            raise WorkCopyError(f"OUTSIDE_WORKCOPY:{rel}")
        return p

    def changed(self) -> list[str]:
        out = _cgit(self.gitdir, self.dst, ["status", "--porcelain", "-z", "--untracked-files=all"]).stdout
        parts = out.split("\0")
        res: list[str] = []
        i = 0
        while i < len(parts):
            e = parts[i]
            i += 1
            if len(e) < 4:
                continue
            xy, p = e[:2], e[3:]
            res.append(p)
            if "R" in xy or "C" in xy:  # bei Rename folgt der alte Pfad als eigener Eintrag
                if i < len(parts) and parts[i]:
                    res.append(parts[i])
                i += 1
        res.extend(self._git_entries())  # .git* im Arbeitsbaum (git selbst listet sie nie)
        return sorted(set(res))

    def _git_entries(self) -> list[str]:
        out: list[str] = []
        for d, dirs, files in os.walk(self.dst):
            for n in dirs + files:
                # nur echte .git-Eintraege (Repo-Einschleusung); geaenderte .gitignore/.gitattributes meldet git status
                if n.lower() == ".git":
                    out.append(Path(d, n).relative_to(self.dst).as_posix())
            dirs[:] = [x for x in dirs if not x.lower().startswith(".git") and x != "__pycache__"]
        return out

    def violations(self) -> list[str]:
        """Alles Unzulaessige in der Kopie: Allowlist-Verstoesse, .git*-Eintraege, Symlinks (SYMLINK:<pfad>)."""
        ch = self.changed()
        bad = [f"SYMLINK:{p}" for p in ch if os.path.islink(self.dst / p)]
        return bad + _scope_violations(ch)

    def reset(self) -> None:
        """Kopie auf den Basisstand zuruecksetzen (vor einer Korrekturrunde)."""
        _cgit(self.gitdir, self.dst, ["reset", "-q", "--hard", "HEAD"])
        _cgit(self.gitdir, self.dst, ["clean", "-qfd"])

    def commit(self, message: str) -> str:
        _cgit(self.gitdir, self.dst, ["add", "-A"])
        _cgit(self.gitdir, self.dst, ["commit", "-q", "-m", message])
        return _cgit(self.gitdir, self.dst, ["rev-parse", "HEAD"]).stdout.strip()

    def publish(self, branch: str) -> dict:
        branch = _slug(branch)
        if self.git_root:
            name, n = branch, 1
            while _git(["show-ref", "--verify", "--quiet", f"refs/heads/{name}"], self.root, check=False).returncode == 0:
                n += 1
                name = f"{branch}-{n}"
            _git(["fetch", "--quiet", str(self.gitdir), f"HEAD:refs/heads/{name}"], self.root)
            return {"branch": name}
        pdir = _paths.WORKSPACE / "autobuild" / "patches"
        pdir.mkdir(parents=True, exist_ok=True)
        target = pdir / (branch.replace("/", "_") + ".patch")
        n = 1
        while target.exists():
            n += 1
            target = pdir / f"{branch.replace('/', '_')}-{n}.patch"
        target.write_text(_cgit(self.gitdir, self.dst, ["format-patch", "-1", "--stdout"]).stdout, encoding="utf-8")
        return {"patch": str(target)}

    def remove(self) -> None:
        shutil.rmtree(self.dst, ignore_errors=True)
        shutil.rmtree(self.gitdir, ignore_errors=True)
