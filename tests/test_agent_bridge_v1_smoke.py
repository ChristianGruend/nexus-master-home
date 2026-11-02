#!/usr/bin/env python3
"""12 smoke tests for NEXUS Agent Bridge Protocol v1. No secrets printed."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("NEXUS_PORT", "8299"))
BASE = os.environ.get("NEXUS_BRIDGE_BASE", f"http://127.0.0.1:{PORT}")


def _load_token() -> str:
    env = (os.environ.get("NEXUS_BRIDGE_TOKEN") or "").strip()
    if env:
        return env
    for fp in (
        ROOT / "workspace" / "bridge" / ".token",
        ROOT / "workspace" / "certs" / ".bridge_token",
    ):
        if fp.is_file():
            return fp.read_text(encoding="utf-8").strip()
    raise SystemExit("No NEXUS_BRIDGE_TOKEN / workspace/bridge/.token — start server once first")


def _mask(tok: str) -> str:
    return "..." + tok[-4:] if tok and len(tok) >= 4 else "(leer)"


def req(method: str, path: str, body: dict | None = None, token: str | None = None, timeout: float = 30.0):
    url = BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                payload = {"_raw": raw}
            return resp.status, payload
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"_raw": raw}
        return e.code, payload


def main() -> int:
    tok = _load_token()
    print(f"BASE={BASE} AUTH mask={_mask(tok)}")
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(("PASS" if ok else "FAIL"), name, detail)

    # 1 auth required
    code, body = req("GET", "/api/bridge/health", token=None)
    check("01_health_no_auth_401", code == 401, f"code={code}")

    # 2 health ok
    code, body = req("GET", "/api/bridge/health", token=tok)
    check(
        "02_health_ok",
        code == 200 and body.get("ok") is True and body.get("marker") == "NEXUS_AGENT_BRIDGE_V1",
        f"code={code} marker={body.get('marker')}",
    )

    # 3 capabilities
    code, body = req("GET", "/api/capabilities", token=tok)
    names = [t.get("name") for t in (body.get("tasks") or [])]
    check("03_capabilities", code == 200 and "ping" in names and "git_status" in names, f"n={len(names)}")

    # 4 ping
    code, body = req("POST", "/api/task", {"task": "ping", "params": {"echo": "smoke"}}, token=tok)
    task = (body.get("task") or {})
    ping_id = task.get("id")
    check("04_ping", code == 200 and task.get("status") == "succeeded" and bool(ping_id), f"id={ping_id}")

    # 5 get task
    code, body = req("GET", f"/api/task/{ping_id}", token=tok) if ping_id else (0, {})
    check("05_get_task", code == 200 and (body.get("task") or {}).get("id") == ping_id, f"code={code}")

    # 6 system_status
    code, body = req("POST", "/api/task", {"task": "system_status"}, token=tok)
    check("06_system_status", code == 200 and (body.get("task") or {}).get("status") == "succeeded", f"code={code}")

    # 7 inspect_server
    code, body = req("POST", "/api/task", {"task": "inspect_server"}, token=tok)
    check("07_inspect_server", code == 200 and (body.get("task") or {}).get("status") == "succeeded", f"code={code}")

    # 8 read_file
    code, body = req("POST", "/api/task", {"task": "read_file", "params": {"path": "README.md"}}, token=tok)
    content = ((body.get("task") or {}).get("result") or {}).get("content") or ""
    check("08_read_file", code == 200 and len(content) > 0, f"code={code} len={len(content)}")

    # 9 list_files
    code, body = req("POST", "/api/task", {"task": "list_files", "params": {"path": "nexus"}}, token=tok)
    entries = ((body.get("task") or {}).get("result") or {}).get("entries") or []
    check("09_list_files", code == 200 and len(entries) > 0, f"code={code} n={len(entries)}")

    # 10 git_status + capability_check
    code1, b1 = req("POST", "/api/task", {"task": "git_status"}, token=tok)
    code2, b2 = req("POST", "/api/task", {"task": "capability_check"}, token=tok)
    check(
        "10_git_and_capability",
        code1 == 200 and code2 == 200
        and (b1.get("task") or {}).get("status") == "succeeded"
        and (b2.get("task") or {}).get("status") == "succeeded",
        f"git={code1} cap={code2}",
    )

    # 11 unknown task
    code, body = req("POST", "/api/task", {"task": "rm_rf_evil"}, token=tok)
    check("11_unknown_task", code == 400 and body.get("error") == "UNKNOWN_TASK", f"code={code} err={body.get('error')}")

    # 12 path traversal blocked + chat still open + v1 alias
    code_t, body_t = req(
        "POST", "/api/task",
        {"task": "read_file", "params": {"path": "../Windows/System32/drivers/etc/hosts"}},
        token=tok,
    )
    trav_ok = code_t in (400, 403) and body_t.get("error") in ("PATH_TRAVERSAL", "PATH_DENIED", "INVALID_REQUEST")
    # if nested in task result
    if not trav_ok:
        terr = (body_t.get("task") or {}).get("error_code") or (body_t.get("task") or {}).get("error")
        trav_ok = terr in ("PATH_TRAVERSAL", "PATH_DENIED") or "traversal" in str(body_t).lower()
    code_c, body_c = req("POST", "/api/chat", {"message": "ping"}, token=None)
    chat_ok = code_c == 200 and ("reply" in body_c or body_c.get("success") is True or "error" not in str(body_c.get("error", "")).lower() or True)
    # chat should not be 401
    chat_ok = code_c != 401
    code_v, body_v = req("GET", "/api/v1/status", token=tok)
    v1_ok = code_v == 200 and body_v.get("ok") is True
    check("12_traversal_chat_v1", trav_ok and chat_ok and v1_ok, f"trav={code_t}/{body_t.get('error')} chat={code_c} v1={code_v}")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"SUMMARY {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
