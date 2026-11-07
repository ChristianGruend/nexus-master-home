"""Phase 1 (WP1): Zugriffsschutz fuer Agenten-Endpunkte.

Standard: nur Loopback ohne Proxy-/Tailscale-Header. Optional fuer bewusste
Fernnutzung: NEXUS_AGENT_REMOTE=1 plus Bearer NEXUS_AGENT_TOKEN.
Kein Not-Aus: der Guard ist immer aktiv.
"""
from __future__ import annotations

import hmac
import os

LOCAL_IPS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
PROXY_HEADERS = ("X-Forwarded-For", "Tailscale-User-Login", "X-Forwarded-Host", "Forwarded")


def remote_enabled() -> bool:
    return os.environ.get("NEXUS_AGENT_REMOTE", "0").strip() == "1"


def is_loopback(ip: str, headers) -> bool:
    if str(ip or "") not in LOCAL_IPS:
        return False
    return not any(headers.get(h) for h in PROXY_HEADERS)


def token_ok(headers) -> bool:
    expected = (os.environ.get("NEXUS_AGENT_TOKEN") or "").strip()
    if not expected:
        return False
    raw = str(headers.get("Authorization") or "")
    got = raw[7:].strip() if raw[:7].lower() == "bearer " else str(headers.get("X-NEXUS-Agent-Token") or "").strip()
    return bool(got) and hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8"))


def allow(ip: str, headers) -> tuple[bool, str]:
    """(erlaubt, grund). Fail-closed: ohne Loopback und ohne Token -> False."""
    if is_loopback(ip, headers):
        return True, "LOOPBACK"
    if remote_enabled() and token_ok(headers):
        return True, "REMOTE_TOKEN"
    return False, "LOOPBACK_OR_TOKEN_REQUIRED"
