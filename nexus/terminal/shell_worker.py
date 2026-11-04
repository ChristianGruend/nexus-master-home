"""Read-only shell worker adapter (tools/worker.ps1 v1.4).

Runs the PowerShell worker without a shell, sends the request as JSON on stdin and
normalizes the JSON answer. Paths come from WORKER_PS1 / WORKER_SANDBOX / WORKER_AUDIT
(environment first, repo defaults second). No string concatenation of commands.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from nexus.common.paths import IS_WINDOWS, ROOT

WHITELIST = (
    "ls", "cat", "grep", "find", "wc", "head", "tail",
    "stat", "du", "df", "pwd", "date", "uname", "echo",
)
MAX_TIMEOUT = 60
DEFAULT_TIMEOUT = 20
HARD_MARGIN = 10
MAX_OUTPUT = 65536
DENIED_EXIT = 126


def worker_ps1() -> Path:
    raw = (os.environ.get("WORKER_PS1") or "").strip()
    return Path(raw).expanduser() if raw else ROOT / "tools" / "worker.ps1"


def sandbox_dir() -> Path:
    raw = (os.environ.get("WORKER_SANDBOX") or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / "sandbox"


def audit_file() -> Path:
    raw = (os.environ.get("WORKER_AUDIT") or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / "logs" / "shell-audit.jsonl"


def powershell_exe() -> str:
    raw = (os.environ.get("WORKER_POWERSHELL") or "").strip()
    if raw:
        return raw
    if IS_WINDOWS:
        return "powershell"
    return shutil.which("pwsh") or "powershell"


def _clamp_timeout(value: Any) -> int:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        seconds = DEFAULT_TIMEOUT
    return max(1, min(seconds, MAX_TIMEOUT))


def _normalize_args(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    raise ValueError("args must be a list of strings")


def _result(**kw) -> dict:
    base = {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
            "truncated": False, "timed_out": False, "error": None,
            "denied": False, "duration_ms": 0}
    base.update(kw)
    return base


def _denied(error: str) -> dict:
    return _result(ok=False, exit_code=DENIED_EXIT, error=error, denied=True)


def _emit(agent: str, cmd: str, args: list[str], result: dict, emit_live: bool) -> None:
    if not emit_live:
        return
    try:
        from nexus.common.live_events import emit
        text = (cmd + " " + " ".join(args)).strip()
        emit(agent, "shell_readonly", text, target="Sandbox", ok=bool(result.get("ok")),
             data={"cmd": cmd, "args": args, "ok": bool(result.get("ok")),
                   "denied": bool(result.get("denied")), "exit_code": result.get("exit_code"),
                   "duration_ms": result.get("duration_ms"), "error": result.get("error")})
    except Exception:
        pass


def _aether_check(agent: str, emit_live: bool) -> dict:
    try:
        from nexus.integrations import aether_supervisor as aether
        return aether.check_shell_guard(agent=agent, emit_live=emit_live)
    except Exception:
        return {"blocked": False, "error": "AETHER_GUARD_UNAVAILABLE"}


def run(cmd: Any, args: Any = None, cwd: Any = ".", timeout: Any = DEFAULT_TIMEOUT,
        *, agent: str = "Hermes", emit_live: bool = True) -> dict:
    """Execute one whitelisted read-only command through worker.ps1."""
    name = str(cmd or "").strip().lower()
    try:
        arg_list = _normalize_args(args)
    except ValueError as exc:
        result = _denied(f"DENIED: INVALID_ARGS: {exc}")
        _emit(agent, name, [], result, emit_live)
        return result

    if name not in WHITELIST:
        result = _denied(f"DENIED: UNKNOWN_COMMAND: {name}")
        _emit(agent, name, arg_list, result, emit_live)
        return result

    guard = _aether_check(agent, emit_live)
    if guard.get("blocked"):
        result = _denied("DENIED: TOOL_TEMPORARILY_LOCKED: " + str(guard.get("reason") or "shell guard"))
        _emit(agent, name, arg_list, result, emit_live)
        return result

    ps1 = worker_ps1()
    if not ps1.is_file():
        result = _result(ok=False, error=f"WORKER_PS1_MISSING: {ps1}")
        _emit(agent, name, arg_list, result, emit_live)
        return result

    sandbox = sandbox_dir()
    try:
        sandbox.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result = _result(ok=False, error=f"WORKER_SANDBOX_UNAVAILABLE: {type(exc).__name__}: {exc}")
        _emit(agent, name, arg_list, result, emit_live)
        return result

    seconds = _clamp_timeout(timeout)
    request = {"cmd": name, "args": arg_list, "cwd": str(cwd or "."), "timeout": seconds}
    argv = [powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "--json"]
    started = time.time()
    try:
        proc = subprocess.run(
            argv,
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=seconds + HARD_MARGIN,
            cwd=str(sandbox),
        )
    except subprocess.TimeoutExpired as exc:
        result = _result(ok=False, exit_code=124, timed_out=True,
                         error=f"TIMEOUT: worker exceeded {seconds}+{HARD_MARGIN}s")
        result["stdout"] = str(exc.stdout or "")[:MAX_OUTPUT]
        result["stderr"] = str(exc.stderr or "")[:MAX_OUTPUT]
        result["duration_ms"] = int((time.time() - started) * 1000)
        _emit(agent, name, arg_list, result, emit_live)
        _aether_check(agent, emit_live)
        return result
    except FileNotFoundError:
        result = _result(ok=False, error=f"WORKER_PS1_MISSING: {ps1}")
        result["duration_ms"] = int((time.time() - started) * 1000)
        _emit(agent, name, arg_list, result, emit_live)
        return result
    except OSError as exc:
        result = _result(ok=False, error=f"WORKER_START_FAILED: {type(exc).__name__}: {exc}")
        result["duration_ms"] = int((time.time() - started) * 1000)
        _emit(agent, name, arg_list, result, emit_live)
        return result

    duration_ms = int((time.time() - started) * 1000)
    raw = (proc.stdout or "").strip()
    if not raw:
        result = _result(ok=False, exit_code=proc.returncode,
                         error="INVALID_WORKER_JSON: empty stdout", duration_ms=duration_ms)
        result["stderr"] = (proc.stderr or "")[:MAX_OUTPUT]
        _emit(agent, name, arg_list, result, emit_live)
        _aether_check(agent, emit_live)
        return result
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        result = _result(ok=False, exit_code=proc.returncode,
                         error=f"INVALID_WORKER_JSON: {exc}: {raw[:200]}", duration_ms=duration_ms)
        result["stderr"] = (proc.stderr or "")[:MAX_OUTPUT]
        _emit(agent, name, arg_list, result, emit_live)
        _aether_check(agent, emit_live)
        return result
    if not isinstance(payload, dict):
        result = _result(ok=False, exit_code=proc.returncode,
                         error="INVALID_WORKER_JSON: not an object", duration_ms=duration_ms)
        _emit(agent, name, arg_list, result, emit_live)
        return result

    stdout = str(payload.get("stdout") or "")
    stderr = str(payload.get("stderr") or "")
    truncated = bool(payload.get("truncated"))
    if len(stdout) > MAX_OUTPUT:
        stdout = stdout[:MAX_OUTPUT]
        truncated = True
    if len(stderr) > MAX_OUTPUT:
        stderr = stderr[:MAX_OUTPUT]
        truncated = True
    error = payload.get("error")
    exit_code = payload.get("exit_code", proc.returncode)
    ok = bool(payload.get("ok"))
    if not ok and not error:
        error = "WORKER_ERROR"
    result = _result(
        ok=ok,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        truncated=truncated,
        timed_out=bool(payload.get("timed_out")),
        error=error,
        duration_ms=duration_ms,
    )
    result["denied"] = bool(str(error or "").startswith("DENIED")) or int(exit_code or 0) == DENIED_EXIT
    _emit(agent, name, arg_list, result, emit_live)
    if not result["ok"]:
        _aether_check(agent, emit_live)
    return result
