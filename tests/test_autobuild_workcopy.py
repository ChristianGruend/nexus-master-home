"""Arbeitskopie des Autobuild (Stufe 2.4): create/changed/commit/publish, Branch- und Patch-Variante."""
from pathlib import Path

import pytest

from nexus.autobuild.workcopy import WorkCopy, WorkCopyError
from tests._ab_env import git, make_env

FILES = {"nexus/a.py": "A = 1\n", "nexus/sub/b.py": "B = 1\n", "workspace/geheim.txt": "x\n", "tests/test_x.py": "def test_x():\n    pass\n"}


def test_create_git_root_clones_head(tmp_path, monkeypatch):
    root = make_env(tmp_path, monkeypatch, FILES)
    (root / "uncommitted.txt").write_text("nur lokal", encoding="utf-8")
    wc = WorkCopy.create("c1", root)
    assert wc.dst == tmp_path / "shared" / "autobuild" / "c1"
    assert wc.git_root and wc.base == git(root, "rev-parse", "HEAD").strip()
    assert git(wc.gitdir, "rev-parse", "HEAD").strip() == wc.base
    assert (wc.dst / "nexus/a.py").read_text() == "A = 1\n" and not (wc.dst / "uncommitted.txt").exists()
    assert wc.changed() == []
    assert not (wc.dst / ".git").exists() and wc.gitdir.is_dir() and wc.gitdir.parent == tmp_path / "ws" / "autobuild" / "gitdirs"
    wc.remove()
    assert not wc.dst.exists() and not wc.gitdir.exists()


def test_create_without_git_copies_tree(tmp_path, monkeypatch):
    root = make_env(tmp_path, monkeypatch, {**FILES, "nexus/__pycache__/x.pyc": "zz", ".venv/lib.py": "v"}, git_repo=False)
    wc = WorkCopy.create("c2", root)
    assert not wc.git_root and (wc.dst / "nexus/sub/b.py").is_file()
    assert not (wc.dst / "workspace").exists() and not (wc.dst / ".venv").exists() and not (wc.dst / "nexus/__pycache__").exists()
    assert wc.changed() == []


def test_path_containment(tmp_path, monkeypatch):
    wc = WorkCopy.create("c3", make_env(tmp_path, monkeypatch, FILES))
    assert wc.path("nexus/a.py") == (wc.dst / "nexus/a.py").resolve()
    with pytest.raises(WorkCopyError):
        wc.path("../../etc/passwd")


def test_changed_commit_publish_branch(tmp_path, monkeypatch):
    root = make_env(tmp_path, monkeypatch, FILES)
    wc = WorkCopy.create("c4", root)
    (wc.dst / "nexus/a.py").write_text("A = 2\n", encoding="utf-8")
    (wc.dst / "nexus/neu.py").write_text("N = 1\n", encoding="utf-8")
    (wc.dst / "nexus/sub/b.py").unlink()
    (wc.dst / "nexus/__pycache__").mkdir()
    (wc.dst / "nexus/__pycache__/a.pyc").write_text("zz")  # per exclude ignoriert
    assert wc.changed() == ["nexus/a.py", "nexus/neu.py", "nexus/sub/b.py"]
    sha = wc.commit("autobuild: T1 test")
    assert len(sha) == 40
    pub = wc.publish("autobuild/t1")
    assert pub == {"branch": "autobuild/t1"}
    assert git(root, "rev-parse", "autobuild/t1").strip() == sha
    assert git(root, "show", "autobuild/t1:nexus/a.py") == "A = 2\n"
    assert git(root, "status", "--porcelain") == ""  # Arbeitsbaum des Live-Repos unberuehrt
    assert (root / "nexus/a.py").read_text() == "A = 1\n"
    # Suffix bei bestehendem Branch
    assert wc.publish("autobuild/t1") == {"branch": "autobuild/t1-2"}
    assert wc.publish("autobuild/t1") == {"branch": "autobuild/t1-3"}


def test_changed_rename(tmp_path, monkeypatch):
    wc = WorkCopy.create("c5", make_env(tmp_path, monkeypatch, FILES))
    from nexus.autobuild.workcopy import _cgit
    _cgit(wc.gitdir, wc.dst, ["mv", "nexus/a.py", "nexus/a2.py"])
    assert set(wc.changed()) == {"nexus/a.py", "nexus/a2.py"}


def test_reset(tmp_path, monkeypatch):
    wc = WorkCopy.create("c6", make_env(tmp_path, monkeypatch, FILES))
    (wc.dst / "nexus/a.py").write_text("X\n")
    (wc.dst / "nexus/neu.py").write_text("N\n")
    wc.reset()
    assert wc.changed() == [] and (wc.dst / "nexus/a.py").read_text() == "A = 1\n"


def test_publish_patch_without_git(tmp_path, monkeypatch):
    root = make_env(tmp_path, monkeypatch, FILES, git_repo=False)
    wc = WorkCopy.create("c7", root)
    (wc.dst / "nexus/a.py").write_text("A = 5\n", encoding="utf-8")
    wc.commit("autobuild: T9 x")
    pub = wc.publish("autobuild/t9")
    p = Path(pub["patch"])
    assert p == tmp_path / "ws" / "autobuild" / "patches" / "autobuild_t9.patch"
    txt = p.read_text(encoding="utf-8")
    assert "+A = 5" in txt and "nexus/a.py" in txt
    assert not (root / ".git").exists() and (root / "nexus/a.py").read_text() == "A = 1\n"


@pytest.mark.parametrize("git_repo", [True, False])
def test_no_git_in_worktree_after_create(tmp_path, monkeypatch, git_repo):
    wc = WorkCopy.create("n1", make_env(tmp_path, monkeypatch, FILES, git_repo=git_repo))
    assert not (wc.dst / ".git").exists() and (wc.gitdir / "HEAD").is_file()
    assert wc.changed() == []


def test_attack_git_config_in_worktree(tmp_path, monkeypatch):
    root = make_env(tmp_path, monkeypatch, FILES)
    wc = WorkCopy.create("atk", root)
    pwned = tmp_path / "pwned"
    (wc.dst / ".git").mkdir()
    (wc.dst / ".git" / "config").write_text(f"[core]\n\tfsmonitor = touch {pwned}\n", encoding="utf-8")
    (wc.dst / "nexus/sub/.git").mkdir()
    (wc.dst / "nexus/sub/.git" / "config").write_text(f"[core]\n\tfsmonitor = touch {pwned}\n", encoding="utf-8")
    (wc.dst / "nexus/a.py").write_text("A = 2\n", encoding="utf-8")
    v = wc.violations()
    assert any(x.startswith(".git") for x in v) and any(x.startswith("nexus/sub/.git") for x in v)
    wc.commit("x")
    wc.publish("autobuild/atk")
    assert not pwned.exists()


def test_symlink_is_violation(tmp_path, monkeypatch):
    wc = WorkCopy.create("sl", make_env(tmp_path, monkeypatch, FILES))
    (wc.dst / "nexus/x.py").symlink_to("/etc/hostname")
    assert "nexus/x.py" in wc.changed()
    assert "SYMLINK:nexus/x.py" in wc.violations()


@pytest.mark.parametrize("git_repo", [True, False])
def test_tracked_gitignore_is_not_a_violation(tmp_path, monkeypatch, git_repo):
    """Eine .gitignore aus dem Basisstand darf keinen Zyklus scheitern lassen (nur Änderungen daran)."""
    files = {**FILES, ".gitignore": "*.log\n", "nexus/.gitattributes": "* text=auto\n"}
    wc = WorkCopy.create("c9", make_env(tmp_path, monkeypatch, files, git_repo=git_repo))
    assert wc.changed() == [] and wc.violations() == []
    (wc.dst / ".gitignore").write_text("*\n", encoding="utf-8")
    assert any(v.startswith(".gitignore") for v in wc.violations())
