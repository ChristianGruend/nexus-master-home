"""Hilfen fuer Autobuild-Tests: temporaeres git-Repo als ROOT, Runner-Volume und Logs unter tmp_path."""
import json
import subprocess
from pathlib import Path

from nexus.autobuild import loop

_ID = ["-c", "user.name=t", "-c", "user.email=t@localhost"]


def git(cwd, *args):
    return subprocess.run(["git", *_ID, *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout


def make_env(tmp_path, monkeypatch, files: dict, backlog: list | None = None, git_repo: bool = True) -> Path:
    """Legt tmp_path/repo (ROOT) mit `files` an, setzt loop.ROOT/DIR/... um. Liefert das ROOT."""
    root = tmp_path / "repo"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if git_repo:
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "basis")
    d = tmp_path / "ab"
    d.mkdir()
    monkeypatch.setattr(loop, "ROOT", root)
    for k, v in (("DIR", d), ("BACKLOG", d / "backlog.json"), ("CYCLES", d / "cycles.jsonl"),
                 ("PROGRESS", d / "progress.md"), ("STOP_FILE", d / "STOP")):
        monkeypatch.setattr(loop, k, v)
    monkeypatch.setenv("NEXUS_RUNNER_SHARED", str(tmp_path / "shared"))
    monkeypatch.delenv("NEXUS_RUNNER_SOCKET", raising=False)
    from nexus.autobuild import workcopy
    monkeypatch.setattr(workcopy._paths, "WORKSPACE", tmp_path / "ws")
    if backlog is not None:
        (d / "backlog.json").write_text(json.dumps(backlog), encoding="utf-8")
    return root
