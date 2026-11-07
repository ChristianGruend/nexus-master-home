"""Umgebung für Kindprozesse ohne Geheimnisse (Tokens, Passwörter, API-Keys, SSH-Agent)."""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "APIKEY", "CREDENTIAL", "PRIVATE_KEY", "ACCESS_KEY")
SECRET_EXACT = frozenset({"SSH_AUTH_SOCK", "GPG_AGENT_INFO", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NEXUS_MAIL_APP_PASSWORD"})


def is_secret_name(name: str) -> bool:
    n = name.upper()
    return n in SECRET_EXACT or any(m in n for m in SECRET_MARKERS)


def scrub(env: Mapping[str, str] | None = None, *, keep: Iterable[str] = (), **extra: str) -> dict[str, str]:
    """Kopie von env (Standard os.environ) ohne Secret-Variablen; `keep` nimmt Namen bewusst aus; `extra` wird zuletzt gesetzt."""
    keep_set = {k.upper() for k in keep}
    src = os.environ if env is None else env
    out = {k: v for k, v in src.items() if k.upper() in keep_set or not is_secret_name(k)}
    out.update(extra)
    return out
