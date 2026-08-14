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

"""MCP protocol tests for department tools (in-process ClientSession ↔ FastMCP)."""

import pytest
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session

from app.domain.department_models import CoaSnapshot, EmSnapshot, LimsSnapshot
from app.domain.department_tools import (
    ALLOWED_DEPARTMENT_TOOLS,
    CASE_MEMORY_TOOLS,
    COA_FETCH,
    EM_QUERY,
    LIMS_QUERY,
    STERILITY_TOOLS,
)
from app.mcp.client import (
    DepartmentToolCallError,
    call_department_tool,
    resolve_department_mcp_target,
    validate_mcp_url,
)
from app.mcp.department_server import (
    build_department_mcp,
    has_bearer_authorization,
    mcp_request_requires_bearer,
)


@pytest.mark.asyncio
async def test_department_mcp_exposes_exactly_the_named_tools() -> None:
    """Department reads plus sterility clock access. No other surface."""
    mcp = build_department_mcp()
    async with create_connected_server_and_client_session(mcp) as session:
        listed = await session.list_tools()
    names = {tool.name for tool in listed.tools}
    assert names == ALLOWED_DEPARTMENT_TOOLS | STERILITY_TOOLS | CASE_MEMORY_TOOLS
    assert "postgres-execute-sql" not in names


@pytest.mark.asyncio
async def test_lims_query_returns_structured_catalog_row() -> None:
    payload = await call_department_tool(
        LIMS_QUERY, tenant_id="aurex", batch_id="AX-2291"
    )
    lims = LimsSnapshot.model_validate(payload)
    assert lims.system == "cloud_sql"
    assert lims.rows


@pytest.mark.asyncio
async def test_em_and_coa_tools_return_connector_shapes() -> None:
    em = EmSnapshot.model_validate(
        await call_department_tool(EM_QUERY, tenant_id="aurex", batch_id="AX-2291")
    )
    coa = CoaSnapshot.model_validate(
        await call_department_tool(COA_FETCH, tenant_id="aurex", batch_id="AX-2291")
    )
    assert em.system == "bigquery"
    assert coa.system == "drive"
    assert coa.signed is True


@pytest.mark.asyncio
async def test_unknown_catalog_row_is_tool_error() -> None:
    mcp = build_department_mcp()
    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            LIMS_QUERY, {"tenant_id": "aurex", "batch_id": "NO-SUCH-BATCH"}
        )
    assert result.isError is True


@pytest.mark.asyncio
async def test_unregistered_tool_name_is_error() -> None:
    mcp = build_department_mcp()
    async with create_connected_server_and_client_session(mcp) as session:
        try:
            result = await session.call_tool(
                "postgres-execute-sql",
                {"tenant_id": "aurex", "batch_id": "AX-2291"},
            )
        except McpError:
            return
    assert result.isError is True


@pytest.mark.asyncio
async def test_collector_client_rejects_unlisted_tool_name() -> None:
    with pytest.raises(DepartmentToolCallError, match="may not call"):
        await call_department_tool(
            "postgres-execute-sql", tenant_id="aurex", batch_id="AX-2291"
        )


def test_mcp_target_fails_closed_without_url_or_inprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COUNTERSIGN_MCP_INPROCESS", raising=False)
    monkeypatch.delenv("COUNTERSIGN_MCP_URL", raising=False)
    with pytest.raises(DepartmentToolCallError, match="not configured"):
        resolve_department_mcp_target()


def test_mcp_url_must_be_https_except_loopback() -> None:
    assert validate_mcp_url("https://mcp.example.com/mcp").startswith("https://")
    validate_mcp_url("http://127.0.0.1:8080/mcp")
    with pytest.raises(DepartmentToolCallError, match="https"):
        validate_mcp_url("http://mcp.example.com/mcp")


def test_https_url_wins_over_inprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COUNTERSIGN_MCP_INPROCESS", "1")
    monkeypatch.setenv("COUNTERSIGN_MCP_URL", "https://mcp.example.com/mcp")
    assert resolve_department_mcp_target() == "https://mcp.example.com/mcp"


def test_mcp_http_requires_bearer_except_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_ALLOW_UNAUTHENTICATED", raising=False)
    assert mcp_request_requires_bearer(method="GET", path="/health") is False
    assert mcp_request_requires_bearer(method="GET", path="/") is False
    assert mcp_request_requires_bearer(method="POST", path="/mcp") is True
    assert has_bearer_authorization(None) is False
    assert has_bearer_authorization("Bearer token") is True
    assert has_bearer_authorization("Basic x") is False
