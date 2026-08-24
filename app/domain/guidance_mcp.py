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

"""Controlled-guidance retrieval reached through MCP, for the
Runtime-deployed workflow.

Same reason as ``case_memory_mcp.py``: an Agent Identity SPIFFE principal
cannot hold direct GCP resource IAM credentials, so the case workflow
reaches Vertex AI RAG Engine-backed guidance retrieval through the
department MCP service (which already holds a real service-account
credential) instead of calling Vertex AI RAG Engine itself.
"""

from __future__ import annotations

from app.domain.department_tools import CONTROLLED_GUIDANCE_QUERY
from app.domain.guidance_models import GuidanceCorpus, GuidanceQueryResult
from app.mcp.client import call_guidance_tool


def _unwrap(payload: dict) -> dict:
    """FastMCP wraps some structured returns under ``{"result": ...}``."""
    if "result" in payload and "tenant_id" not in payload:
        return payload["result"]
    return payload


class McpControlledGuidanceStore:
    """Async-only client for the ``controlled_guidance_query`` MCP tool."""

    async def query_async(
        self, *, tenant_id: str, corpus: GuidanceCorpus, query: str, top_k: int = 5
    ) -> GuidanceQueryResult:
        payload = await call_guidance_tool(
            CONTROLLED_GUIDANCE_QUERY,
            {
                "tenant_id": tenant_id,
                "corpus": corpus.value,
                "query": query,
                # Stamped from the same tenant, not trusted from the caller
                # — the case workflow only ever reads its own classified
                # case, same discipline as McpCaseMemoryStore.query_async.
                "case_tenant_id": tenant_id,
                "top_k": top_k,
            },
        )
        if not payload:
            return GuidanceQueryResult(
                tenant_id=tenant_id, query=query, citations=[], configured=False
            )
        return GuidanceQueryResult.model_validate(_unwrap(payload))
