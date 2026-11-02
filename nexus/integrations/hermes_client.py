"""Native Hermes tool -> existing authenticated NEXUS REST bridge (not MCP).

Only explicitly reviewed PC tasks and bounded artifacts are exposed. No shell, arbitrary
URL, approval decision or device action is reachable through this tool. Patches
(patch_file) are limited to the released artifact/code area, require a non-empty
old_text/new_text pair and create a backup snapshot at the executor before applying.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from nexus.bridge.code_sandbox import code_path as _normalize_code_path
from nexus.bridge.outcomes import evaluate
from nexus.bridge.permissions import TASK_PERMISSIONS

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8299"
TASKS = ("ping", "git_status", "inspect_server", "capability_check", "read_file", "list_files", "create_file",
         "patch_file", "python_run_project_script", "hermes_check_code", "hermes_run_code_tests", "mouse_move",
         "hermes_check_web", "hermes_publish_preview", "hermes_prepare_nexus_patch", "hermes_test_nexus_patch",
         # PRIVILEGED: Hermes kann nur eine lokale Freigabe anfordern; ausgeführt wird erst nach Freigabe.
         "nexus_apply_patch", "nexus_restart")
ARTIFACT_ROOT = "workspace/projects/hermes"
ARTIFACT_PATTERN = r"workspace/projects/hermes/[A-Za-z0-9_-]{1,80}\.(txt|md|json)"
CODE_ROOT = "workspace/projects/hermes/code"
PROBE_SCRIPT = "workspace/scripts/hermes_core_probe.py"
READ_PATH = "docs/BRIDGE_OUTCOMES.md"
SCHEMA = "nexus.hermes-result.v1"
CONTRACT = "nexus.executor-outcome.v1"


class IntegrationError(Exception):
    """Messages are static codes, never raw transport or executor errors."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise IntegrationError("REDIRECT_DENIED")


def _correlation(value: Any = None) -> str:
    if value is None:
        return str(uuid.uuid4())
    if type(value) is not str:
        raise IntegrationError("INVALID_CORRELATION_ID")
    try:
        parsed = str(uuid.UUID(value))
    except ValueError:
        raise IntegrationError("INVALID_CORRELATION_ID") from None
    if parsed != value:
        raise IntegrationError("INVALID_CORRELATION_ID")
    return parsed


def _artifact_path(value: Any) -> bool:
    if type(value) is not str or re.fullmatch(ARTIFACT_PATTERN, value) is None:
        return False
    if Path(value).stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
        return False
    path = ROOT / value
    return path.resolve() == ROOT.resolve() / value and not path.is_symlink()


def _code_path(value: Any, *, suffix: str | None = None) -> bool:
    normalized = _normalize_code_path(value)
    if normalized is None:
        return False
    if suffix is not None and not normalized.lower().endswith(suffix):
        return False
    path = ROOT / normalized
    return path.resolve() == ROOT.resolve() / normalized and not path.is_symlink()


def _released_path(value: Any) -> bool:
    """Write/read scope: the flat artifact area OR the code area."""
    return _artifact_path(value) or _code_path(value)


def validate_task(task: Any, params: Any) -> None:
    if type(task) is not str or task not in TASKS or type(params) is not dict:
        raise IntegrationError("TASK_NOT_ALLOWED")
    if task == "ping":
        if set(params) - {"echo"}:
            raise IntegrationError("PARAMS_NOT_ALLOWED")
        if "echo" in params and (type(params["echo"]) is not str or
                                re.fullmatch(r"[A-Za-z0-9_.:-]{0,64}", params["echo"]) is None):
            raise IntegrationError("INVALID_ECHO")
    elif task == "read_file":
        if set(params) != {"path"} or (params["path"] != READ_PATH and not _released_path(params["path"])):
            raise IntegrationError("READ_PATH_NOT_ALLOWED")
    elif task == "list_files":
        if params != {"path": ARTIFACT_ROOT} or (ROOT / ARTIFACT_ROOT).resolve() != ROOT.resolve() / ARTIFACT_ROOT:
            raise IntegrationError("READ_PATH_NOT_ALLOWED")
    elif task == "create_file":
        if set(params) != {"path", "content"} or not _released_path(params["path"]):
            raise IntegrationError("WRITE_PATH_NOT_ALLOWED")
        if type(params["content"]) is not str or len(params["content"].encode("utf-8")) > 64000:
            raise IntegrationError("INVALID_CONTENT")
    elif task == "patch_file":
        if set(params) != {"path", "old_text", "new_text"} or not _released_path(params["path"]):
            raise IntegrationError("WRITE_PATH_NOT_ALLOWED")
        old, new = params["old_text"], params["new_text"]
        if (type(old) is not str or type(new) is not str or not old.strip()
                or len(old.encode("utf-8")) > 32000 or len(new.encode("utf-8")) > 64000):
            raise IntegrationError("INVALID_CONTENT")
        if old == new:
            raise IntegrationError("INVALID_CONTENT")
    elif task == "python_run_project_script":
        if params != {"script": PROBE_SCRIPT}:
            raise IntegrationError("SCRIPT_NOT_ALLOWED")
    elif task == "hermes_check_code":
        if set(params) != {"path"} or not _code_path(params["path"], suffix=".py"):
            raise IntegrationError("CHECK_PATH_NOT_ALLOWED")
    elif task == "hermes_run_code_tests":
        if not {"path"} <= set(params) or set(params) - {"path", "timeout"}:
            raise IntegrationError("PARAMS_NOT_ALLOWED")
        if not _code_path(params["path"], suffix=".py"):
            raise IntegrationError("TEST_PATH_NOT_ALLOWED")
        timeout = params.get("timeout")
        if timeout is not None and (type(timeout) is not int or not 1 <= timeout <= 60):
            raise IntegrationError("INVALID_TIMEOUT")
    elif task == "hermes_check_web":
        if set(params) != {"path"} or not _code_path(params["path"], suffix=".html"):
            raise IntegrationError("CHECK_PATH_NOT_ALLOWED")
    elif task == "hermes_publish_preview":
        if (set(params) != {"path", "check_task_id"} or not _code_path(params["path"], suffix=".html")
                or type(params["check_task_id"]) is not str
                or re.fullmatch(r"NX-\d{8}-\d{4,}", params["check_task_id"]) is None):
            raise IntegrationError("PARAMS_NOT_ALLOWED")
    elif task in ("hermes_prepare_nexus_patch", "hermes_test_nexus_patch"):
        from nexus.bridge.dev_executors import nexus_target_allowed
        if type(params.get("patch_id")) is not str or re.fullmatch(r"PATCH-[0-9a-f]{12}", params["patch_id"]) is None:
            raise IntegrationError("PARAMS_NOT_ALLOWED")
        if task == "hermes_test_nexus_patch" and set(params) != {"patch_id"}:
            raise IntegrationError("PARAMS_NOT_ALLOWED")
        if task == "hermes_prepare_nexus_patch":
            files = params.get("files")
            if (set(params) != {"patch_id", "goal", "files"} or type(params["goal"]) is not str
                    or type(files) is not list or not 1 <= len(files) <= 5
                    or any(type(f) is not dict or set(f) != {"path", "content"} or type(f["content"]) is not str
                           or nexus_target_allowed(f["path"]) is None for f in files)):
                raise IntegrationError("PATCH_NOT_ALLOWED")
    elif task in ("nexus_apply_patch", "nexus_restart"):
        allowed = {"patch_id", "approval_id"} if task == "nexus_apply_patch" else {"approval_id"}
        if set(params) - allowed or (task == "nexus_apply_patch" and (type(params.get("patch_id")) is not str
                                     or re.fullmatch(r"PATCH-[0-9a-f]{12}", params["patch_id"]) is None)):
            raise IntegrationError("PARAMS_NOT_ALLOWED")
        aid = params.get("approval_id")
        if aid is not None and (type(aid) is not str or re.fullmatch(r"AP-[0-9a-f]{16}", aid) is None):
            raise IntegrationError("INVALID_APPROVAL_ID")
    elif task == "mouse_move":
        if not {"x", "y"} <= set(params) or set(params) - {"x", "y", "approval_id"}:
            raise IntegrationError("PARAMS_NOT_ALLOWED")
        if type(params["x"]) is not int or type(params["y"]) is not int:
            raise IntegrationError("INVALID_COORDINATES")
        aid = params.get("approval_id")
        if aid is not None and (type(aid) is not str or re.fullmatch(r"AP-[0-9a-f]{16}", aid) is None):
            raise IntegrationError("INVALID_APPROVAL_ID")
    elif params:
        raise IntegrationError("PARAMS_NOT_ALLOWED")


def failure(code: str, correlation_id: str | None = None, *, sent: bool = False) -> dict:
    return {"schema": SCHEMA, "success": False, "status": "verification_unknown" if sent else "blocked",
            "error_code": code, "correlation_id": correlation_id, "task_id": None,
            "executed_by": None, "requested_by": "hermes", "request_sent": sent,
            "exit_code": None, "stdout": None, "stderr": None,
            "retry_safe": not sent}


class NexusClient:
    def __init__(self):
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())
        self._token = None

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        if self._token is None:
            try:
                self._token = (ROOT / "workspace/bridge/.token").read_text(encoding="utf-8").strip()
            except OSError:
                raise IntegrationError("BRIDGE_TOKEN_UNAVAILABLE") from None
        if not self._token:
            raise IntegrationError("BRIDGE_TOKEN_UNAVAILABLE")
        request = Request(BASE + path, method=method,
                          data=json.dumps(body).encode("utf-8") if body is not None else None,
                          headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"})
        try:
            try:
                response = self._opener.open(request, timeout=20)
            except HTTPError as exc:
                response = exc
            with response:
                code = response.code
                raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise IntegrationError("RESPONSE_TOO_LARGE")
            data = json.loads(raw)
            if type(data) is not dict:
                raise IntegrationError("INVALID_RESPONSE")
            return code, data
        except (URLError, TimeoutError, OSError):
            raise IntegrationError("TRANSPORT_UNAVAILABLE") from None
        except (ValueError, UnicodeError):
            raise IntegrationError("INVALID_RESPONSE") from None

    def health(self) -> dict:
        code, data = self._request("GET", "/api/bridge/health")
        ok = code == 200 and data.get("ok") is True
        return {"schema": SCHEMA, "success": ok, "operation": "health", "http_status": code,
                "executed_by": "nexus_bridge", "version": data.get("version"),
                "outcome_contract": data.get("outcome_contract"),
                "runtime_outcomes_verified": ok and data.get("outcome_contract") == CONTRACT}

    def capabilities(self) -> dict:
        code, data = self._request("GET", "/api/capabilities")
        exposed = {x.get("name") for x in data.get("tasks", []) if type(x) is dict and x.get("permission") in ("READ_ONLY", "SAFE_WRITE")}
        return {"schema": SCHEMA, "success": code == 200 and data.get("success") is True,
                "operation": "capabilities", "http_status": code, "executed_by": "nexus_bridge",
                "transport": "REST", "allowed_tasks": [x for x in TASKS if x in exposed],
                "generic_shell": False, "writes_enabled": True,
                "write_scope": ARTIFACT_ROOT + " + " + CODE_ROOT, "create_only": False,
                "patch_enabled": True, "patch_scope": "released artifact/code area",
                "patch_guard": "old_text != new_text, non-empty, size capped, backup snapshot at executor",
                "code_execution_default": "blocked_unless_nexus_code_exec_and_containment",
                "approval_decisions_enabled": False}

    def find_approval(self, task: str, params: dict) -> str | None:
        """Newest approved (unconsumed) local approval for task+params, if any.

        PRIVILEGED tasks are one-shot; the caller re-submits with this approval_id.
        Matching on params keeps a release bound to the exact order.
        """
        code, data = self._request("GET", "/api/bridge/approvals?status=approved")
        if code != 200:
            return None
        for item in data.get("approvals") or []:
            if (type(item) is dict and item.get("task") == task
                    and item.get("params") == params and item.get("status") == "approved"):
                aid = item.get("approval_id")
                if type(aid) is str and re.fullmatch(r"AP-[0-9a-f]{16}", aid):
                    return aid
        return None

    @staticmethod
    def _record(data: dict) -> dict:
        record = data.get("task")
        if type(record) is not dict:
            raise IntegrationError("TASK_RECORD_MISSING")
        task_id = record.get("id")
        if type(task_id) is not str or re.fullmatch(r"NX-\d{8}-\d{4,}", task_id) is None:
            raise IntegrationError("INVALID_TASK_ID")
        return record

    def _normalize(self, record: dict, correlation: str, code: int) -> dict:
        name, params = record.get("task"), record.get("params")
        validate_task(name, params)
        if record.get("source") != "hermes-core:" + correlation or record.get("client") != "hermes-core":
            raise IntegrationError("CORRELATION_MISMATCH")
        if record.get("permission") != TASK_PERMISSIONS[name].value:
            raise IntegrationError("PERMISSION_MISMATCH")
        execution = record.get("execution")
        instance_verified = (type(execution) is dict
                             and type(execution.get("host")) is str and bool(execution["host"])
                             and type(execution.get("pid")) is int and execution["pid"] > 0
                             and type(execution.get("executor")) is str and bool(execution["executor"])
                             and type(execution.get("instance_id")) is str)
        if instance_verified:
            try:
                instance_verified = str(uuid.UUID(execution["instance_id"])) == execution["instance_id"]
            except ValueError:
                instance_verified = False
        raw = record.get("result")
        outcome = evaluate(name, params, raw)
        original_status = record.get("status")
        success = code == 200 and original_status == "succeeded" and outcome.status == "succeeded"
        error = record.get("error_code") or outcome.error_code
        if name == "ping" and type(raw) is dict and raw.get("task_id") != record["id"]:
            success, error = False, "EXECUTOR_TASK_ID_MISMATCH"
        if success and not instance_verified:
            success, error = False, "EXECUTION_INSTANCE_UNVERIFIED"
        if not success and not error:
            error = "VERIFICATION_UNKNOWN"
        fields = raw if type(raw) is dict else {}
        return {"schema": SCHEMA, "success": success, "status": "succeeded" if success else "failed",
                "backend_status": original_status, "error_code": error, "task": name, "params": params,
                "task_id": record["id"], "correlation_id": correlation, "executed_by": "nexus_executor",
                "requested_by": "hermes", "transport": "REST", "http_status": code,
                "execution_instance": record.get("execution"),
                "execution_instance_verified": bool(instance_verified),
                "exit_code": fields.get("returncode", fields.get("exit_code")),
                "stdout": fields.get("stdout"), "stderr": fields.get("stderr"), "result": raw,
                "verification": outcome.verdict, "request_sent": True, "retry_safe": False}

    def submit(self, task: str, params: dict, correlation_id: str | None = None) -> dict:
        correlation = _correlation(correlation_id)
        validate_task(task, params)
        code, data = self._request("POST", "/api/task", {
            "task": task, "params": params, "source": "hermes-core:" + correlation, "client": "hermes-core"})
        record = self._record(data)
        if record.get("task") != task or record.get("params") != params:
            raise IntegrationError("REQUEST_RESULT_MISMATCH")
        # Independently fetch the real task record; no optimistic POST-only success.
        get_code, fetched = self._request("GET", "/api/task/" + record["id"])
        observed = self._record(fetched)
        if get_code != 200 or any(observed.get(k) != record.get(k) for k in
                                  ("id", "task", "params", "source", "client", "permission", "status", "result", "error_code", "execution")):
            raise IntegrationError("TASK_READBACK_MISMATCH")
        normalized = self._normalize(observed, correlation, code)
        normalized["task_readback_verified"] = True
        return normalized

    def status(self, task_id: str, correlation_id: str) -> dict:
        correlation = _correlation(correlation_id)
        if type(task_id) is not str or re.fullmatch(r"NX-\d{8}-\d{4,}", task_id) is None:
            raise IntegrationError("INVALID_TASK_ID")
        code, data = self._request("GET", "/api/task/" + task_id)
        if code != 200:
            raise IntegrationError("TASK_STATUS_UNAVAILABLE")
        record = self._record(data)
        if record["id"] != task_id:
            raise IntegrationError("TASK_ID_MISMATCH")
        normalized = self._normalize(record, correlation, code)
        normalized["task_readback_verified"] = True
        return normalized

    def public(self, data: dict) -> dict:
        """Protect agent context: no credentials, private git filenames or user paths.

        Raw executor data stays with NEXUS. Redaction is explicit, never fabricated
        empty output; internal normalization keeps the exact original streams.
        """
        redacted = False
        def clean(value, key=""):
            nonlocal redacted
            if key.lower() in {"token", "token_mask", "authorization", "password", "api_key", "secret"}:
                redacted = True
                return "[REDACTED]"
            if key == "stdout" and data.get("task") == "git_status" and value:
                redacted = True
                return None
            if isinstance(value, dict):
                return {k: clean(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [clean(x) for x in value]
            if isinstance(value, str):
                result = value.replace(self._token, "[REDACTED]") if self._token else value
                result = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", result)
                result = re.sub(r"(?i)[A-Z]:[\\/]Users[\\/][^\s\"<>]+", "[PRIVATE_PATH]", result)
                redacted |= result != value
                return result
            return value
        result = clean(data)
        result["redacted"] = redacted
        return result


def invoke(args: dict) -> dict:
    """Live-Monitor-Wrapper (20261028) um _invoke_impl: jeder Bridge-Tool-Aufruf + Ergebnis als Event."""
    try:
        from nexus.common.live_events import emit as _emit
        _a = args if isinstance(args, dict) else {}
        _emit("Hermes", "tool", f"{_a.get('operation')}:{_a.get('task') or ''}", target="Bridge",
              data={"params": {k: (v if k != "content" else f"<{len(str(v))} Zeichen>") for k, v in (_a.get("params") or {}).items()}} if isinstance(_a.get("params"), dict) else None)
    except Exception:
        _emit = None
    result = _invoke_impl(args)
    try:
        if _emit:
            r = result if isinstance(result, dict) else {}
            _emit("Bridge", "ergebnis", f"{r.get('backend_status') or ('OK' if r.get('success') else r.get('error_code') or 'FEHLER')} task_id={r.get('task_id')}",
                  target="Hermes", ok=r.get("success") is True,
                  data={"readback": r.get("task_readback_verified"), "instance": r.get("execution_instance_verified"), "error": r.get("error_code")})
    except Exception:
        pass
    return result


def _invoke_impl(args: dict) -> dict:
    """Tool boundary: no raw exceptions or secrets enter Hermes logs/prompts."""
    correlation = None
    sent = False
    client = NexusClient()
    try:
        if type(args) is not dict:
            raise IntegrationError("INVALID_ARGUMENTS")
        operation = args.get("operation")
        if operation == "health" and set(args) == {"operation"}:
            result = client.health()
        elif operation == "capabilities" and set(args) == {"operation"}:
            result = client.capabilities()
        elif operation == "submit" and not set(args) - {"operation", "task", "params", "correlation_id"}:
            correlation = _correlation(args.get("correlation_id"))
            validate_task(args.get("task"), args.get("params"))
            sent = True  # failure after this boundary may have executed; never auto-retry
            result = client.submit(args["task"], args["params"], correlation)
        elif operation == "status" and set(args) == {"operation", "task_id", "correlation_id"}:
            correlation = _correlation(args["correlation_id"])
            result = client.status(args["task_id"], correlation)
        else:
            raise IntegrationError("OPERATION_NOT_ALLOWED")
        return client.public(result)
    except IntegrationError as exc:
        return failure(str(exc), correlation, sent=sent)
    except Exception:
        return failure("INTEGRATION_ERROR", correlation, sent=sent)
