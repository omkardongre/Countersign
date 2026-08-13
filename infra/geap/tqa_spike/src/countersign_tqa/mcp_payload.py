"""Parse MCP JSON-RPC tools/call from an HTTP body the gateway streams."""

from __future__ import annotations

import json
from typing import Any

from countersign_tqa.policy import McpToolCall

_TOOLS_CALL = "tools/call"


def _text(body: bytes) -> str:
    return body.decode("utf-8", errors="replace").strip()


def _first_json_object(text: str) -> Any | None:
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    for line in text.splitlines():
        candidate = line.strip()
        if candidate.startswith("data:"):
            candidate = candidate[5:].strip()
        if candidate.startswith("{"):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    return None


def parse_mcp_tool_call(body: bytes) -> McpToolCall | None:
    """Return a tools/call tuple, or None if this body is not tools/call."""
    payload = _first_json_object(_text(body))
    if not isinstance(payload, dict):
        return None
    method = payload.get("method")
    if method != _TOOLS_CALL:
        return None
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return McpToolCall(name="", tenant_id=None, case_tenant_id=None, lifecycle_state=None)
    name = params.get("name") or ""
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    tenant = arguments.get("tenant_id")
    case_tenant = arguments.get("case_tenant_id")
    if case_tenant is None:
        case_tenant = arguments.get("acting_tenant_id")
    state = arguments.get("lifecycle_state")
    return McpToolCall(
        name=str(name) if name is not None else "",
        tenant_id=str(tenant) if tenant is not None else None,
        case_tenant_id=str(case_tenant) if case_tenant is not None else None,
        lifecycle_state=str(state) if state is not None else None,
    )
