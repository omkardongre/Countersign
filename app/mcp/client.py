# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""MCP client used by QC and supply collectors.

Runtime must set COUNTERSIGN_MCP_URL (https Streamable HTTP, typically ``…/mcp``)
so calls egress through Agent Gateway. In-process FastMCP is only used when
COUNTERSIGN_MCP_INPROCESS is explicitly set (unit tests / local graph runs).
Omitting both fails closed — collectors must not skip the gateway by default.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from app.domain.department_tools import ALLOWED_DEPARTMENT_TOOLS
from app.mcp.department_server import get_department_mcp

MCP_URL_ENV = "COUNTERSIGN_MCP_URL"
MCP_INPROCESS_ENV = "COUNTERSIGN_MCP_INPROCESS"
_LOCAL_HTTP_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class DepartmentToolCallError(Exception):
    """MCP tools/call failed or returned no structured payload."""


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def validate_mcp_url(url: str) -> str:
    """Require https, or http only to loopback for local tests."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return url
    if parsed.scheme == "http" and host in _LOCAL_HTTP_HOSTS:
        return url
    raise DepartmentToolCallError(
        "COUNTERSIGN_MCP_URL must be https, or http to localhost."
    )


def resolve_department_mcp_target() -> FastMCP | str:
    """Gateway URL, or in-process server if explicitly opted in."""
    url = os.environ.get(MCP_URL_ENV, "").strip()
    if url:
        return validate_mcp_url(url)
    if _env_flag(MCP_INPROCESS_ENV):
        return get_department_mcp()
    raise DepartmentToolCallError(
        "Department MCP is not configured: set COUNTERSIGN_MCP_URL "
        f"(https through Agent Gateway) or {MCP_INPROCESS_ENV}=1 for local tests."
    )


def _error_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return " ".join(parts) or "MCP tool returned isError with no text"


@asynccontextmanager
async def _department_session() -> AsyncIterator[ClientSession]:
    target = resolve_department_mcp_target()
    if isinstance(target, str):
        async with streamable_http_client(target) as (read, write, _session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return
    async with create_connected_server_and_client_session(target) as session:
        yield session


async def call_department_tool(
    name: str,
    *,
    tenant_id: str,
    batch_id: str,
    lifecycle_state: str = "classified",
    case_tenant_id: str | None = None,
) -> dict[str, Any]:
    """Call one named department tool over MCP. Fail closed on errors."""
    if name not in ALLOWED_DEPARTMENT_TOOLS:
        raise DepartmentToolCallError(
            f"Collectors may not call {name!r}; allowed={sorted(ALLOWED_DEPARTMENT_TOOLS)}"
        )
    acting = case_tenant_id if case_tenant_id is not None else tenant_id
    async with _department_session() as session:
        result = await session.call_tool(
            name,
            {
                "tenant_id": tenant_id,
                "case_tenant_id": acting,
                "lifecycle_state": lifecycle_state,
                "batch_id": batch_id,
            },
        )
    if result.isError:
        raise DepartmentToolCallError(_error_text(result))
    payload = result.structuredContent
    if not isinstance(payload, dict) or not payload:
        raise DepartmentToolCallError(
            f"{name} returned no structured content for {tenant_id}/{batch_id}."
        )
    return payload
