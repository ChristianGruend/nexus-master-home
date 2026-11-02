"""Bearer auth for Agent Bridge (NEXUS_BRIDGE_TOKEN) — separate from assistant token."""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from nexus.common.paths import ROOT, WORKSPACE

ENV_NAME = "NEXUS_BRIDGE_TOKEN"
TOKEN_REL_CANDIDATES = (
    WORKSPACE / "bridge" / ".token",
    WORKSPACE / "certs" / ".bridge_token",
)


def mask_token(tok: str) -> str:
    if not tok:
        return "(leer)"
    if len(tok) <= 8:
        return "..." + tok[-4:]
    return "..." + tok[-4:]


def token_path() -> Path:
    """Preferred path for persisted bridge token."""
    return WORKSPACE / "bridge" / ".token"


def _read_token_file(fp: Path) -> str:
    try:
        if fp.is_file():
            return fp.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _chmod_private(fp: Path) -> None:
    try:
        os.chmod(fp, 0o600)
    except Exception:
        pass
    # Best-effort Windows ACL lockdown via icacls (non-fatal)
    if os.name == "nt":
        try:
            import subprocess
            user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
            if user:
                subprocess.run(
                    ["icacls", str(fp), "/inheritance:r", f"/grant:r", f"{user}:R"],
                    capture_output=True,
                    timeout=8,
                    check=False,
                )
        except Exception:
            pass


def ensure_bridge_token() -> tuple[str, Path | None, bool]:
    """
    Load NEXUS_BRIDGE_TOKEN from env or file; if missing, generate once to workspace/bridge/.token.
    Returns (token, path_used_or_written, generated_now).
    Also sets os.environ[NEXUS_BRIDGE_TOKEN] for the process.
    """
    env = (os.environ.get(ENV_NAME) or "").strip()
    if env:
        # Prefer documenting existing file if present
        for fp in TOKEN_REL_CANDIDATES:
            if _read_token_file(fp) == env:
                return env, fp, False
        return env, None, False

    for fp in TOKEN_REL_CANDIDATES:
        tok = _read_token_file(fp)
        if tok:
            os.environ[ENV_NAME] = tok
            return tok, fp, False

    # Generate once
    fp = token_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_hex(32)
    fp.write_text(tok, encoding="utf-8", newline="\n")
    _chmod_private(fp)
    os.environ[ENV_NAME] = tok
    return tok, fp, True


def get_bridge_token() -> str:
    tok, _, _ = ensure_bridge_token()
    return tok


def verify_bearer(authorization_header: str | None, expected: str | None = None) -> bool:
    """Constant-time Bearer check. False if token unset or header missing/wrong."""
    expected = (expected if expected is not None else get_bridge_token()).strip()
    if not expected:
        return False
    hdr = authorization_header or ""
    if not hdr.lower().startswith("bearer "):
        return False
    got = hdr[7:].strip()
    if not got:
        return False
    try:
        return hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8"))
    except Exception:
        return False


def project_root() -> Path:
    return ROOT.resolve()
