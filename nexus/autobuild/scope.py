"""Pfad-Allowlist für Autobuild (Stufe 2.4): was der Selbstbau ändern darf."""
from __future__ import annotations

import fnmatch

ALLOWED = ("nexus/**", "tests/test_auto_*.py")
PROTECTED = (
    "nexus/safety/**",
    "nexus/autobuild/**",
    "nexus/runner/**",
    "nexus/terminal/guard.py",
    "nexus/common/agent_guard.py",
    "nexus/common/api_auth.py",
    "nexus/common/net_guard.py",
    "nexus/common/proc_env.py",
    "nexus/bridge/permissions.py",
    "nexus/bridge/auth.py",
    "nexus/chat/server.py",
)


def normalize(rel) -> str | None:
    """Relativen Pfad vereinheitlichen (/ statt \\, ohne führendes ./); None bei leer, absolut,
    Laufwerksbuchstabe, '..', leeren Teilen oder .git*-Teilen."""
    if rel is None:
        return None
    s = str(rel).replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    if not s.strip() or s.startswith("/") or (len(s) >= 2 and s[1] == ":"):
        return None
    parts = s.split("/")
    if any(p in ("", ".", "..") or p.lower().startswith(".git") for p in parts):
        return None
    return s


def _match(path: str, pattern: str) -> bool:
    # Vergleich ohne Groß/Klein: auf Windows/macOS ist nexus/Safety/x dieselbe Datei wie nexus/safety/x
    path, pattern = path.lower(), pattern.lower()
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return fnmatch.fnmatchcase(path, pattern) and path.count("/") == pattern.count("/")


def check_path(rel) -> tuple[bool, str]:
    """(True, normalisierter Pfad) oder (False, Grund)."""
    p = normalize(rel)
    if p is None:
        return False, "UNGUELTIGER_PFAD"
    for pattern in PROTECTED:
        if _match(p, pattern):
            return False, "GESCHUETZT:" + pattern
    if not any(_match(p, pattern) for pattern in ALLOWED):
        return False, "AUSSERHALB_ALLOWLIST"
    return True, p


def violations(paths) -> list[str]:
    """Alle Pfade, die check_path ablehnt, als '<pfad>: <grund>' (Reihenfolge wie Eingabe)."""
    out = []
    for path in paths:
        ok, reason = check_path(path)
        if not ok:
            out.append(f"{path}: {reason}")
    return out
