"""Thin client helpers for talking to Nexus bridge / assistant locally."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class ChatGPTBridgeClient:
    """Local-first helper. Never requires paid OpenAI."""

    def __init__(
        self,
        bridge_base: str = "http://127.0.0.1:8299",
        token: str | None = None,
        assistant_token: str | None = None,
    ) -> None:
        self.bridge_base = bridge_base.rstrip("/")
        self.token = token or os.environ.get("NEXUS_BRIDGE_TOKEN") or ""
        self.assistant_token = assistant_token or os.environ.get("NEXUS_ASSISTANT_TOKEN") or ""

    def _req(self, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict[str, Any]:
        url = self.bridge_base + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        tok = token if token is not None else self.token
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return json.loads(raw)
            except Exception:
                return {"success": False, "error": "HTTPError", "code": e.code, "body": raw[:500]}

    def health(self) -> dict[str, Any]:
        return self._req("GET", "/api/bridge/health")

    def capabilities(self) -> dict[str, Any]:
        return self._req("GET", "/api/capabilities")

    def handshake(self) -> dict[str, Any]:
        return self._req("POST", "/api/message", {"message": "NEXUS_BRIDGE_OK handshake", "source": "chatgpt_client"})

    def task(self, name: str, params: dict | None = None, approval_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"task": name, "params": params or {}, "source": "chatgpt_client"}
        if approval_id:
            body["approval_id"] = approval_id
        return self._req("POST", "/api/task", body)

    def assistant_chat(self, message: str, session_id: str | None = None) -> dict[str, Any]:
        body = {"message": message, "source": "chatgpt", "session_id": session_id}
        return self._req("POST", "/api/assistant", body, token=self.assistant_token)
