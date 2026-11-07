"""Zentrale API-Zugangsprüfung (Stufe 1.2): alles außer UI-Seiten braucht Loopback, Session-Cookie oder Token."""
from __future__ import annotations

import hmac
import os
import secrets
import threading
from pathlib import Path

from nexus.common import agent_guard
from nexus.common.paths import WORKSPACE

COOKIE_NAME = "nx_session"
PUBLIC_EXACT = frozenset({
    "/", "/index.html", "/m", "/m/", "/m.html", "/m2", "/m2/", "/m2.html",
    "/m-classic", "/m-classic/", "/mail", "/mail.html", "/studio", "/studio.html",
    "/nexusall", "/nexusall/", "/nexusall.html", "/swarm", "/swarm.html",
    "/approvals", "/approvals.html", "/live", "/live_monitor.html",
    "/manifest.webmanifest", "/sw.js", "/login", "/api/health",
})
PUBLIC_PREFIX = ("/static/",)
OWN_AUTH_EXACT = frozenset({"/api/assistant", "/api/task", "/api/message", "/api/capabilities"})
OWN_AUTH_PREFIX = ("/api/nexus-os/", "/api/bridge/", "/api/task/")

_LOCK = threading.Lock()


def _secrets_dir() -> Path:
    return WORKSPACE / "secrets"


def _read_or_create(name: str) -> str:
    with _LOCK:
        path = _secrets_dir() / name
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if len(value) >= 32:
                return value
        value = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return value


def session_secret() -> str:
    return _read_or_create("api_session.txt")


def api_token() -> str:
    env = (os.environ.get("NEXUS_API_TOKEN") or "").strip()
    if len(env) >= 32:
        return env
    return _read_or_create("api_token.txt")


def session_cookie(secure: bool = False) -> str:
    base = f"{COOKIE_NAME}={session_secret()}; Path=/; HttpOnly; SameSite=Strict"
    if secure:
        return base + "; Secure"
    return base


def has_session(cookie_header: str | None) -> bool:
    if not cookie_header:
        return False
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition("=")
        if key.strip() == COOKIE_NAME:
            return hmac.compare_digest(value.strip().encode("utf-8"), session_secret().encode("utf-8"))
    return False


def bearer(headers) -> str:
    raw = str(headers.get("Authorization") or "")
    if raw[:7].lower() == "bearer ":
        return raw[7:].strip()
    return ""


def token_ok(headers) -> bool:
    value = bearer(headers)
    if not value:
        return False
    return hmac.compare_digest(value.encode("utf-8"), api_token().encode("utf-8"))


def human_approval(headers) -> tuple[bool, str]:
    """Freigaben nur durch einen Menschen im Browser-UI (Stufe 2.1a).

    Ein Agent hat Bridge-Token bzw. API-Token, aber keinen HttpOnly-UI-Cookie und kann aus dem
    Browser keine same-origin-Anfrage stellen. Verlangt: Sec-Fetch-Site same-origin, Header
    X-NEXUS-UI: approve und einen gueltigen Cookie (lokal nx_term_ui, entfernt nx_session nach /login).
    """
    if headers.get("Sec-Fetch-Site") != "same-origin" or headers.get("X-NEXUS-UI") != "approve":
        return False, "NUR_UI"
    cookie = headers.get("Cookie")
    try:
        from nexus.terminal import guard as _tg
        if _tg.check_ui_cookie(cookie):
            return True, "UI_COOKIE"
    except Exception:
        pass
    if has_session(cookie):
        return True, "SESSION"
    return False, "KEIN_UI_COOKIE"


def is_public(path: str) -> bool:
    if path in PUBLIC_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIX)


def has_own_auth(path: str) -> bool:
    if path in OWN_AUTH_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in OWN_AUTH_PREFIX)


def check(path: str, ip: str, headers) -> tuple[bool, str]:
    if is_public(path):
        return True, "PUBLIC"
    if has_own_auth(path):
        return True, "OWN_AUTH"
    if headers.get("Sec-Fetch-Site") == "cross-site":
        return False, "CROSS_SITE"
    if agent_guard.is_loopback(ip, headers):
        return True, "LOOPBACK"
    if has_session(headers.get("Cookie")):
        return True, "SESSION"
    if token_ok(headers):
        return True, "TOKEN"
    return False, "AUTH_REQUIRED"


if __name__ == "__main__":
    # Zeigt den API-Token für /login bzw. Authorization: Bearer (nur lokal ausführen).
    print(api_token())
