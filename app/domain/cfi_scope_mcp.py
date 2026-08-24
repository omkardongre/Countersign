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

"""``sibling_batch_query`` reached through MCP, for the CFI Runtime.

Same reason as ``case_memory_mcp.py``: an Agent Identity SPIFFE principal
cannot hold the department catalog/Cloud SQL credential this read needs, so
the scope-expansion agent's tool closure reaches it through the department
MCP service.
"""

from __future__ import annotations

from app.domain.cfi_models import SiblingBatchQueryResult
from app.mcp.client import call_sibling_batch_tool


def _unwrap(payload: dict) -> dict:
    """FastMCP wraps some structured returns under ``{"result": ...}``."""
    if "result" in payload and "tenant_id" not in payload:
        return payload["result"]
    return payload


class McpSiblingBatchQuery:
    """Async-only client for the ``sibling_batch_query`` MCP tool."""

    async def query_async(
        self, *, tenant_id: str, batch_id: str, shared_attribute: str
    ) -> SiblingBatchQueryResult:
        payload = await call_sibling_batch_tool(
            {
                "tenant_id": tenant_id,
                "batch_id": batch_id,
                "shared_attribute": shared_attribute,
                # Stamped from the same tenant, not trusted from the
                # caller: the CFI graph only ever queries its own scope.
                "case_tenant_id": tenant_id,
            }
        )
        return SiblingBatchQueryResult.model_validate(_unwrap(payload))
