"""Local HTTP handlers for /api/automation* (NOT on Funnel allowlist)."""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from nexus.automation.routines import routines
from nexus.automation.scheduler import scheduler
from nexus.bridge.permissions import TASK_PERMISSIONS


def is_automation_get(path: str) -> bool:
    p = (path or "").split("?", 1)[0]
    return p == "/api/automation" or p.startswith("/api/automation/")


def is_automation_post(path: str) -> bool:
    return is_automation_get(path)


def handle_get(handler: Any, path: str, qs: dict | None = None) -> None:
    qs = qs or {}
    p = (path or "").split("?", 1)[0]

    if p == "/api/automation":
        lim = 50
        try:
            lim = int((qs.get("limit") or ["50"])[0])
        except Exception:
            lim = 50
        items = [r.to_dict(include_log=False) for r in routines().list(limit=lim)]
        return handler._json(200, {
            "success": True,
            "ok": True,
            "local_only": True,
            "funnel_exposed": False,
            "backbone": "bridge_tasks",
            "scheduler": scheduler().status(),
            "routines": items,
            "count": len(items),
            "registered_actions": sorted(TASK_PERMISSIONS.keys()),
        })

    if p == "/api/automation/scheduler":
        return handler._json(200, {"success": True, "scheduler": scheduler().status()})

    if p.startswith("/api/automation/"):
        rest = p[len("/api/automation/"):]
        parts = [x for x in rest.split("/") if x]
        if not parts:
            return handler._json(400, {"success": False, "error": "id_required"})
        rid = parts[0]
        if rid == "scheduler":
            return handler._json(200, {"success": True, "scheduler": scheduler().status()})
        rec = routines().get(rid)
        if not rec:
            return handler._json(404, {"success": False, "error": "not_found", "id": rid})
        if len(parts) > 1 and parts[1] == "log":
            return handler._json(200, {
                "success": True,
                "id": rid,
                "log": rec.log,
                "error": rec.error,
                "error_code": rec.error_code,
                "status": rec.status,
            })
        return handler._json(200, {"success": True, "routine": rec.to_dict(include_log=False)})

    return handler._json(404, {"success": False, "error": "not_found", "path": path})


def handle_post(handler: Any, path: str, body: dict | None = None) -> None:
    body = body or {}
    p = (path or "").split("?", 1)[0]

    if p == "/api/automation":
        try:
            rec = routines().create(
                name=str(body.get("name") or body.get("action") or "routine"),
                action=str(body.get("action") or body.get("task") or ""),
                trigger=str(body.get("trigger") or "manual"),
                params=body.get("params") if isinstance(body.get("params"), dict) else {},
                interval_sec=body.get("interval_sec"),
                enabled=bool(body.get("enabled", True)),
                note=str(body.get("note") or ""),
                approval_id=(str(body["approval_id"]) if body.get("approval_id") else None),
            )
        except ValueError as e:
            return handler._json(400, {"success": False, "error": str(e)})
        except Exception as e:
            return handler._json(500, {"success": False, "error": str(e)})
        return handler._json(200, {"success": True, "routine": rec.to_dict(include_log=False)})

    if p == "/api/automation/scheduler/start":
        return handler._json(200, {
            "success": True,
            "scheduler": scheduler().start(),
            "warning": "LOCAL ONLY — not on Funnel allowlist; PRIVILEGED never auto-runs",
        })

    if p == "/api/automation/scheduler/stop":
        return handler._json(200, {"success": True, "scheduler": scheduler().stop()})

    if p.startswith("/api/automation/"):
        rest = p[len("/api/automation/"):]
        parts = [x for x in rest.split("/") if x]
        if not parts:
            return handler._json(400, {"success": False, "error": "id_required"})
        rid = parts[0]
        action = parts[1] if len(parts) > 1 else ""

        if action == "run":
            try:
                aid = body.get("approval_id")
                rec = routines().run(
                    rid,
                    trigger_override=str(body.get("trigger") or "api"),
                    approval_id=(str(aid) if aid else None),
                )
            except KeyError:
                return handler._json(404, {"success": False, "error": "not_found", "id": rid})
            except Exception as e:
                return handler._json(400, {"success": False, "error": str(e)})
            out = {
                "success": True,
                "routine": rec.to_dict(include_log=False),
            }
            if rec.status == "awaiting_approval":
                out["approval_required"] = True
                out["approval_id"] = rec.approval_id
                out["ui"] = "/approvals"
            return handler._json(200, out)

        if action == "cancel":
            try:
                rec = routines().cancel(rid)
            except KeyError:
                return handler._json(404, {"success": False, "error": "not_found", "id": rid})
            return handler._json(200, {"success": True, "routine": rec.to_dict(include_log=False)})

        if action == "enable":
            try:
                rec = routines().set_enabled(rid, True)
            except KeyError:
                return handler._json(404, {"success": False, "error": "not_found", "id": rid})
            return handler._json(200, {"success": True, "routine": rec.to_dict(include_log=False)})

        if action == "disable":
            try:
                rec = routines().set_enabled(rid, False)
            except KeyError:
                return handler._json(404, {"success": False, "error": "not_found", "id": rid})
            return handler._json(200, {"success": True, "routine": rec.to_dict(include_log=False)})

        return handler._json(404, {"success": False, "error": "unknown_action", "action": action})

    return handler._json(404, {"success": False, "error": "not_found", "path": path})
