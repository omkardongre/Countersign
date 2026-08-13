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

"""Sterility clock reached through MCP, for the Runtime-deployed workflow.

Cloud SQL IAM database authentication accepts a user account, a service
account, or a group — not an Agent Identity SPIFFE principal (confirmed
against the live instance: ``gcloud sql users create <effective_identity>``
rejects the name). So the Runtime agent cannot hold Cloud SQL credentials
itself. It reaches the clock through the department MCP service instead,
which already holds a real service-account credential for Cloud SQL and
already mediates LIMS/EM/CoA the same way.

``tick_due`` and ``reset`` are intentionally not implemented here: ticking is
an operator/scheduler action against the store directly (or through this
same MCP surface from the tick service — see ``app/scheduler``), not
something the case workflow itself should trigger on its own behalf.
"""

from __future__ import annotations

from app.domain.department_tools import (
    STERILITY_BIND_RESUME_HANDLE,
    STERILITY_GET,
    STERILITY_START,
)
from app.domain.sterility_models import (
    SterilityIncubationRecord,
    SterilityResumeHandle,
)
from app.mcp.client import call_sterility_tool


def _unwrap(payload: dict) -> dict:
    """FastMCP wraps some structured returns under ``{"result": ...}``."""
    if "result" in payload and "tenant_id" not in payload:
        return payload["result"]
    return payload


class McpSterilityStore:
    """Async-only: every method is a network call. See ``get_async``/``start_async``."""

    async def get_async(
        self, tenant_id: str, batch_id: str
    ) -> SterilityIncubationRecord | None:
        payload = await call_sterility_tool(
            STERILITY_GET,
            {
                "tenant_id": tenant_id,
                "batch_id": batch_id,
                # Stamped from the same tenant, not trusted from the caller:
                # the case workflow only ever reads its own classified case.
                "case_tenant_id": tenant_id,
            },
        )
        if not payload:
            return None
        unwrapped = _unwrap(payload)
        if not unwrapped:
            return None
        return SterilityIncubationRecord.model_validate(unwrapped)

    async def start_async(
        self,
        *,
        tenant_id: str,
        batch_id: str,
        case_id: str,
        workflow_version: str,
    ) -> SterilityIncubationRecord:
        payload = await call_sterility_tool(
            STERILITY_START,
            {
                "tenant_id": tenant_id,
                "batch_id": batch_id,
                "case_id": case_id,
                "workflow_version": workflow_version,
                "case_tenant_id": tenant_id,
            },
        )
        return SterilityIncubationRecord.model_validate(_unwrap(payload))

    async def bind_resume_handle_async(
        self,
        tenant_id: str,
        batch_id: str,
        handle: SterilityResumeHandle,
    ) -> SterilityIncubationRecord:
        payload = await call_sterility_tool(
            STERILITY_BIND_RESUME_HANDLE,
            {
                "tenant_id": tenant_id,
                "batch_id": batch_id,
                "app_name": handle.app_name,
                "user_id": handle.user_id,
                "session_id": handle.session_id,
                "invocation_id": handle.invocation_id,
                "interrupt_id": handle.interrupt_id,
                "case_tenant_id": tenant_id,
            },
        )
        return SterilityIncubationRecord.model_validate(_unwrap(payload))
