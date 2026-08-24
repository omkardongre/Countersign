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

"""Tenant-bound CFI store client for the Runtime-deployed workflow.

Every operation carries the CFI tenant explicitly and the MCP server checks
that it matches the authenticated case tenant. The Runtime therefore never
opens Cloud SQL and never trusts a tenant embedded only in a model-produced
payload.
"""

from __future__ import annotations

from app.domain.cfi_models import CfiInvestigation
from app.domain.department_tools import CFI_GET, CFI_OPEN, CFI_SAVE
from app.mcp.client import call_cfi_tool


def _unwrap(payload: dict) -> dict:
    """FastMCP wraps some structured returns under ``{"result": ...}``."""
    if "result" in payload and "cfi_id" not in payload:
        return payload["result"]
    return payload


class McpCfiStore:
    """Async-only client; all methods are network calls to department MCP."""

    async def open_async(self, investigation: CfiInvestigation) -> CfiInvestigation:
        payload = await call_cfi_tool(
            CFI_OPEN,
            {
                "tenant_id": investigation.tenant_id,
                "case_tenant_id": investigation.tenant_id,
                "investigation": investigation.model_dump(mode="json"),
            },
        )
        return CfiInvestigation.model_validate(_unwrap(payload or {}))

    async def get_async(
        self, *, cfi_id: str, tenant_id: str
    ) -> CfiInvestigation | None:
        payload = await call_cfi_tool(
            CFI_GET,
            {
                "tenant_id": tenant_id,
                "case_tenant_id": tenant_id,
                "cfi_id": cfi_id,
            },
        )
        if not payload:
            return None
        unwrapped = _unwrap(payload)
        if not unwrapped:
            return None
        return CfiInvestigation.model_validate(unwrapped)

    async def save_async(
        self, investigation: CfiInvestigation
    ) -> CfiInvestigation:
        payload = await call_cfi_tool(
            CFI_SAVE,
            {
                "tenant_id": investigation.tenant_id,
                "case_tenant_id": investigation.tenant_id,
                "investigation": investigation.model_dump(mode="json"),
            },
        )
        return CfiInvestigation.model_validate(_unwrap(payload or {}))
