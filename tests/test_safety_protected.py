import os
import sys

import pytest

from nexus.safety.protected import ProtectedPathError, check, ensure_allowed, is_protected


@pytest.fixture
def env(tmp_path, monkeypatch):
    import nexus.common.paths as _p

    root = tmp_path / "repo"
    ws = root / "workspace"
    home = tmp_path / "home"
    (ws / "secrets").mkdir(parents=True, exist_ok=True)
    (ws / "terminal").mkdir(parents=True, exist_ok=True)
    (ws / "bridge" / "approvals").mkdir(parents=True, exist_ok=True)
    (ws / "notes").mkdir(parents=True, exist_ok=True)
    (home / ".ssh").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_p, "ROOT", root)
    monkeypatch.setattr(_p, "WORKSPACE", ws)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    yield (root, ws, home)


@pytest.mark.parametrize(
    "rel",
    [
        "secrets/api_token.txt",
        "terminal/ui_secret.txt",
        "terminal/permissions.json",
        "bridge/.token",
        "bridge/approvals/AP-1.json",
    ],
)
def test_protected_ws(env, rel):
    root, ws, home = env
    assert is_protected(ws / rel)


@pytest.mark.parametrize(
    "rel",
    [
        "notes/a.md",
        "terminal/terminal.jsonl",
        "notes/environment.md",
    ],
)
def test_not_protected_ws(env, rel):
    root, ws, home = env
    assert not is_protected(ws / rel)


def test_protected_root_git(env):
    root, ws, home = env
    assert is_protected(root / ".git" / "config")


def test_protected_home_ssh(env):
    root, ws, home = env
    assert is_protected(home / ".ssh" / "id_ed25519")


def test_protected_env(env):
    root, ws, home = env
    assert is_protected(root / ".env")


def test_protected_env_local(env):
    root, ws, home = env
    assert is_protected(ws / "notes" / ".env.local")


def test_protected_pem(env):
    root, ws, home = env
    assert is_protected(ws / "notes" / "server.pem")


def test_protected_id_rsa(env):
    root, ws, home = env
    assert is_protected(ws / "notes" / "id_rsa.pub")


def test_not_protected_env_example(env):
    root, ws, home = env
    assert not is_protected(root / ".env.example")


def test_not_protected_nexus(env):
    root, ws, home = env
    assert not is_protected(root / "nexus" / "x.py")


def test_dotdot(env):
    root, ws, home = env
    assert is_protected(ws / "notes" / ".." / "secrets" / "x")


@pytest.mark.skipif(sys.platform == "win32", reason="symlink requires admin on Windows")
def test_symlink(env):
    root, ws, home = env
    link = ws / "notes" / "link"
    if link.exists():
        link.unlink()
    os.symlink(ws / "secrets", str(link))
    assert is_protected(ws / "notes" / "link" / "api_token.txt")
    f_link = ws / "notes" / "f.txt"
    if f_link.exists():
        f_link.unlink()
    os.symlink(ws / "terminal" / "ui_secret.txt", str(f_link))
    assert is_protected(f_link)


@pytest.mark.skipif(sys.platform != "win32", reason="case-insensitivity only on Windows")
def test_case_insensitive(env):
    root, ws, home = env
    assert is_protected(str(ws / "SECRETS" / "x"))


def test_check_and_ensure(env):
    root, ws, home = env
    assert check(ws / "secrets" / "api_token.txt") == (False, "PROTECTED_PATH")
    assert check(ws / "notes" / "a.md") == (True, "")
    with pytest.raises(ProtectedPathError):
        ensure_allowed(ws / "secrets" / "api_token.txt")
    assert issubclass(ProtectedPathError, PermissionError)
