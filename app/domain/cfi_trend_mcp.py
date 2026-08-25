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

"""``cfi_trend_query`` reached through MCP, for the CFI Runtime.

Same reason as ``cfi_scope_mcp.py``: an Agent Identity SPIFFE principal
cannot hold the department catalog/Cloud SQL/BigQuery credential this read
needs, so the trend-correlation agent's tool closure reaches it through the
department MCP service.
"""

from __future__ import annotations

from app.domain.cfi_trend_models import CfiTrendResult
from app.domain.department_tools import CFI_TREND_QUERY
from app.mcp.client import call_cfi_tool


def _unwrap(payload: dict) -> dict:
    """FastMCP wraps some structured returns under ``{"result": ...}``."""
    if "result" in payload and "tenant_id" not in payload:
        return payload["result"]
    return payload


class McpCfiTrendQuery:
    """Async-only client for the ``cfi_trend_query`` MCP tool."""

    async def query_async(
        self,
        *,
        tenant_id: str,
        batch_id: str,
        cfi_id: str,
        kind: str,
        compare_batch_id: str | None = None,
        parameter: str | None = None,
        test_code: str | None = None,
    ) -> CfiTrendResult:
        payload = await call_cfi_tool(
            CFI_TREND_QUERY,
            {
                "tenant_id": tenant_id,
                "batch_id": batch_id,
                "cfi_id": cfi_id,
                "case_tenant_id": tenant_id,
                "kind": kind,
                "compare_batch_id": compare_batch_id,
                "parameter": parameter,
                "test_code": test_code,
            },
        )
        return CfiTrendResult.model_validate(_unwrap(payload or {}))
