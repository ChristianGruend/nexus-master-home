"""Generic HTTP(S) tool for the Claude agent.

GET/HEAD are read-only. POST/PUT/PATCH/DELETE require a local terminal
approval by default because they can change remote state. Credentials and
Authorization headers are never echoed back.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

AGENT = "Claude"
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"}
_SAFE_SCHEMES = {"http", "https"}


def _approval_required() -> bool:
    import os
    return (os.environ.get("NEXUS_CLAUDE_HTTP_APPROVAL", "1") or "1").strip() != "0"


def _gate(method: str, url: str, body: bytes, approval_id: str | None) -> dict[str, Any] | None:
    if method in ("GET", "HEAD") or not _approval_required():
        return None
    from nexus.terminal import guard
    label = "http." + method + "(" + url + ",sha256=" + hashlib.sha256(body or b"").hexdigest()[:12] + ")"
    verdict = guard.authorize(AGENT, label, shell="session", approval_id=approval_id,
                              force_approval=True)
    if verdict.get("allowed"):
        return None
    guard.audit("http_blocked", agent=AGENT, action=method, url=url,
                approval_id=verdict.get("approval_id"), status=verdict.get("status"))
    return {"success": False, "status": verdict.get("status"),
            "error": verdict.get("error") or verdict.get("status") or "http_approval_required",
            "approval_id": verdict.get("approval_id"), "action": "http_request",
            "verified": False}


def _redact_headers(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in dict(headers or {}).items():
        if str(key).lower() in _SENSITIVE_HEADERS:
            out[str(key)] = "***"
        else:
            out[str(key)] = str(value)[:300]
    return out


def request(method: str, url: str, *, headers: dict[str, str] | None = None,
            body: Any = None, json_body: Any = None, timeout: float = 30.0,
            approval_id: str | None = None) -> dict[str, Any]:
    method = (method or "GET").strip().upper()
    if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
        return {"success": False, "error": "http_method_not_allowed", "action": "http_request"}
    raw_url = str(url or "").strip()
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return {"success": False, "error": "http_invalid_url", "action": "http_request"}
    if parsed.scheme.lower() not in _SAFE_SCHEMES or not parsed.netloc:
        return {"success": False, "error": "http_scheme_or_host_not_allowed",
                "action": "http_request"}
    if parsed.username or parsed.password:
        return {"success": False, "error": "http_credentials_in_url_not_allowed",
                "action": "http_request"}

    data: bytes | None = None
    send_headers = {str(k): str(v) for k, v in (headers or {}).items()}
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        send_headers.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    if method in ("GET", "HEAD") and data:
        return {"success": False, "error": "http_body_not_allowed_for_get",
                "action": "http_request"}

    blocked = _gate(method, raw_url, data or b"", approval_id)
    if blocked:
        return blocked

    req = urllib.request.Request(raw_url, data=data, method=method, headers=send_headers)
    try:
        with urllib.request.urlopen(req, timeout=max(1.0, min(float(timeout or 30.0), 180.0))) as resp:
            status = int(getattr(resp, "status", 0) or 0)
            payload = resp.read(20000).decode("utf-8", "replace")
            resp_headers = _redact_headers(getattr(resp, "headers", {}))
    except urllib.error.HTTPError as exc:
        payload = ""
        try:
            payload = exc.read(20000).decode("utf-8", "replace")
        except Exception:
            pass
        return {"success": False, "error": "http_status_" + str(getattr(exc, "code", 0)),
                "status_code": int(getattr(exc, "code", 0) or 0),
                "headers": _redact_headers(getattr(exc, "headers", {})),
                "body": payload[:20000], "action": "http_request", "verified": True}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"success": False, "error": "http_transport_" + type(exc).__name__,
                "message": str(exc)[:300], "action": "http_request", "verified": False}

    out: dict[str, Any] = {"success": 200 <= status < 400, "status_code": status,
                           "headers": resp_headers, "body": payload,
                           "action": "http_request", "verified": True,
                           "url": raw_url, "method": method}
    try:
        out["json"] = json.loads(payload)
    except Exception:
        pass
    return out
