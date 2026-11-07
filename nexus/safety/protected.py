"""Geschützte Pfade (Stufe 2.1b): Secrets und Policy-Konfiguration sind für Agenten-Dateiwerkzeuge tabu."""
from __future__ import annotations

import os
from pathlib import Path


PROTECTED_NAMES = (".env",)
PROTECTED_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
PROTECTED_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")


def _protected_dirs() -> list[Path]:
    from nexus.common import paths as _p

    return [
        _p.WORKSPACE / "secrets",
        _p.WORKSPACE / "bridge" / "approvals",
        _p.WORKSPACE / "autobuild" / "gitdirs",
        _p.ROOT / ".git",
        Path.home() / ".ssh",
        Path.home() / ".gnupg",
        Path.home() / ".aws",
        Path.home() / ".config" / "gh",
        Path.home() / ".hermes",
    ]


def _protected_files() -> list[Path]:
    from nexus.common import paths as _p

    return [
        _p.WORKSPACE / "terminal" / "permissions.json",
        _p.WORKSPACE / "terminal" / "approvals.json",
        _p.WORKSPACE / "terminal" / "ui_secret.txt",
        _p.WORKSPACE / "terminal" / "audit.jsonl",
        _p.WORKSPACE / "bridge" / ".token",
        Path.home() / ".netrc",
        Path.home() / ".git-credentials",
    ]


def _norm(p) -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(p))))


def _under(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def is_protected(path) -> bool:
    if not path:
        return False
    n = _norm(path)
    for d in _protected_dirs():
        dn = _norm(d)
        if _under(n, dn):
            return True
    for f in _protected_files():
        fn = _norm(f)
        if n == fn:
            return True
    name = os.path.basename(n).lower()
    if name == ".env":
        return True
    if name.startswith(".env.") and not name == ".env.example":
        return True
    for suffix in PROTECTED_SUFFIXES:
        if name.endswith(suffix.lower()):
            return True
    for prefix in PROTECTED_PREFIXES:
        if name.startswith(prefix.lower()):
            return True
    return False


def check(path) -> tuple[bool, str]:
    if is_protected(path):
        return (False, "PROTECTED_PATH")
    return (True, "")


class ProtectedPathError(PermissionError):
    pass


def ensure_allowed(path) -> None:
    if is_protected(path):
        raise ProtectedPathError(f"PROTECTED_PATH: {path}")
