"""Policy-Engine (Stufe 2.1c): die EINE Quelle fuer Rechtestufen-Vergleiche und die Tool-Registry.

Achsen:
  Stufe  READ_ONLY < SAFE_WRITE < PRIVILEGED   (LEVELS / RANK / allows)
  Ziel   core | runner | host                  (TARGETS, Zuordnung in nexus.policy.targets)

Fail-closed bei unbekannten Stufen:
  * erforderliche Stufe unbekannt  -> Rang von PRIVILEGED (hoechste Huerde)
  * gewaehrte Stufe unbekannt      -> Rang -1 (nichts erlaubt)
Die bisherigen Einzelimplementierungen (bridge.permissions, terminal.guard, safety.gate) liefen bei
unbekannten Werten in einen KeyError; das ist in der Praxis nie eingetreten (nur Enum-/LEVELS-Werte
kommen an), daher ist das Verhalten fuer gueltige Eingaben identisch.
Nur stdlib; Quellmodule (bridge, aether, crew, master, terminal) werden erst in registry()/agent_level()
importiert, damit bridge.permissions diese Datei ohne Importzyklus nutzen kann.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

LEVELS = ("READ_ONLY", "SAFE_WRITE", "PRIVILEGED")
RANK = {lvl: i for i, lvl in enumerate(LEVELS)}
TARGETS = ("core", "runner", "host")
SOURCES = ("bridge", "master", "aether", "crew", "terminal")


def normalize(level: Any) -> str:
    """Enum (auch str-Enum) oder String -> GROSSGESCHRIEBENER String (ohne Validierung)."""
    v = getattr(level, "value", level)
    return str(v if v is not None else "").strip().upper()


def rank(level: Any, default: int | None = None) -> int:
    """Rang einer Stufe. Unbekannt -> `default`, sonst Rang von PRIVILEGED (fail-closed)."""
    r = RANK.get(normalize(level))
    if r is not None:
        return r
    return RANK["PRIVILEGED"] if default is None else default


def allows(required: Any, granted: Any) -> bool:
    """True, wenn `granted` fuer `required` reicht. Unbekanntes required = PRIVILEGED, unbekanntes granted = nichts."""
    return rank(granted, default=-1) >= rank(required)


@dataclass(frozen=True)
class Tool:
    name: str
    level: str
    target: str
    source: str


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    needs_approval: bool
    level: str
    target: str
    reason: str


def _master_level(name: str) -> str:
    from nexus.policy import targets
    if name in targets.MASTER_PRIVILEGED:
        return "PRIVILEGED"
    if name in targets.MASTER_READ_ONLY:
        return "READ_ONLY"
    return "SAFE_WRITE"


@functools.lru_cache(maxsize=1)
def registry() -> dict[str, Tool]:
    """Einmalig gebaute (gecachte) Tool-Registry. Namenskonflikt: hoehere Stufe gewinnt,
    bei Gleichstand die frueher genannte Quelle (bridge, aether, crew, master). Ziel immer aus targets."""
    from nexus.bridge.permissions import TASK_PERMISSIONS
    from nexus.crew.capabilities import ELEVATED_LOCAL, TOOL_CAPABILITY
    from nexus.integrations.aether_supervisor import TOOLS as AETHER_TOOLS
    from nexus.master.tools import KNOWN_TOOLS
    from nexus.policy import targets

    candidates: list[tuple[str, str, str]] = []  # (name, level, source)
    candidates += [(n, normalize(lv), "bridge") for n, lv in TASK_PERMISSIONS.items()]
    candidates += [(n, normalize(td.level), "aether") for n, td in AETHER_TOOLS.items()]
    candidates += [(n, "PRIVILEGED" if lv == ELEVATED_LOCAL else normalize(lv), "crew")
                   for n, lv in TOOL_CAPABILITY.items()]
    candidates += [(n, _master_level(n), "master") for n in sorted(KNOWN_TOOLS)]

    reg: dict[str, Tool] = {}
    for name, level, source in candidates:
        cur = reg.get(name)
        if cur is None or rank(level) > rank(cur.level):
            reg[name] = Tool(name, level, targets.target_for(name), source)
    return reg


def agent_levels() -> dict:
    from nexus.terminal import guard
    return guard.agent_levels()


def agent_level(agent: str) -> str:
    from nexus.terminal import guard
    return guard.agent_level(agent)


def decide(agent: str, tool: str, *, granted: str | None = None) -> Verdict:
    """Entscheidung fuer ein Nicht-Shell-Werkzeug. PRIVILEGED -> needs_approval (allowed erst nach Freigabe)."""
    t = registry().get(str(tool or "").strip().lower())
    if t is None:
        return Verdict(False, False, "UNKNOWN", "", "UNKNOWN_TOOL")
    have = normalize(granted) if granted is not None else agent_level(agent)
    if t.level == "PRIVILEGED":
        return Verdict(False, True, t.level, t.target, "APPROVAL_REQUIRED")
    if not allows(t.level, have):
        return Verdict(False, False, t.level, t.target, "LEVEL_DENIED")
    return Verdict(True, False, t.level, t.target, "OK")
