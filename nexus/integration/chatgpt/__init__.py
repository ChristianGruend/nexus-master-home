"""ChatGPT / external assistant integration helpers (optional, free-local-first)."""
from __future__ import annotations

from nexus.integration.chatgpt.client import ChatGPTBridgeClient
from nexus.integration.chatgpt.schema import openapi_snippet, capabilities_for_gpt

__all__ = ["ChatGPTBridgeClient", "openapi_snippet", "capabilities_for_gpt"]
