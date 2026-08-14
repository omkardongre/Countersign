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

"""Case Memory reached through MCP, for the Runtime-deployed workflow.

Same reason as ``sterility_store_mcp.py``: an Agent Identity SPIFFE
principal cannot hold direct GCP resource IAM credentials, so the case
workflow reaches Firestore-backed Case Memory through the department MCP
service (which already holds a real service-account credential) instead of
opening Firestore itself.
"""

from __future__ import annotations

from app.domain.case_memory_models import CaseMemoryFact, CaseMemoryQueryResult
from app.domain.department_tools import PRIOR_ART_QUERY, PRIOR_ART_WRITE
from app.mcp.client import call_case_memory_tool


def _unwrap(payload: dict) -> dict:
    """FastMCP wraps some structured returns under ``{"result": ...}``."""
    if "result" in payload and "tenant_id" not in payload:
        return payload["result"]
    return payload


class McpCaseMemoryStore:
    """Async-only client for the ``prior_art_*`` MCP tools."""

    async def query_async(
        self, *, tenant_id: str, query: str, top_k: int = 5
    ) -> CaseMemoryQueryResult:
        payload = await call_case_memory_tool(
            PRIOR_ART_QUERY,
            {
                "tenant_id": tenant_id,
                "query": query,
                # Stamped from the same tenant, not trusted from the caller:
                # the case workflow only ever reads its own classified case.
                "case_tenant_id": tenant_id,
                "top_k": top_k,
            },
        )
        if not payload:
            return CaseMemoryQueryResult(
                tenant_id=tenant_id, query=query, facts=[], query_embedded=False
            )
        return CaseMemoryQueryResult.model_validate(_unwrap(payload))

    async def write_fact_async(
        self,
        *,
        tenant_id: str,
        case_id: str,
        batch_id: str,
        fact: str,
        source_uri: str,
        confidence: float,
        fact_id: str | None = None,
    ) -> CaseMemoryFact:
        """Pass ``fact_id`` for a natural idempotency key on a rerun-prone
        caller (``record_case_memory``), so a resume overwrites rather than
        duplicates the same fact.
        """
        arguments = {
            "tenant_id": tenant_id,
            "case_id": case_id,
            "batch_id": batch_id,
            "fact": fact,
            "source_uri": source_uri,
            "confidence": confidence,
            "case_tenant_id": tenant_id,
        }
        if fact_id is not None:
            arguments["fact_id"] = fact_id
        payload = await call_case_memory_tool(PRIOR_ART_WRITE, arguments)
        return CaseMemoryFact.model_validate(_unwrap(payload or {}))
