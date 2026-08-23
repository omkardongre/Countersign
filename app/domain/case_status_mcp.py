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

"""Batch-keyed case status reached through MCP, for the Runtime-deployed
workflow. Same reason as ``sterility_store_mcp.py``/``case_memory_mcp.py``:
an Agent Identity SPIFFE principal cannot hold Cloud SQL IAM credentials
directly, so the case workflow's own writes to ``case_status`` go through
the department MCP service, which already holds the real credential.
"""

from __future__ import annotations

from app.domain.case_status_models import CaseStatusRecord
from app.domain.department_tools import CASE_STATUS_UPSERT
from app.mcp.client import call_case_status_tool


def _unwrap(payload: dict) -> dict:
    """FastMCP wraps some structured returns under ``{"result": ...}``."""
    if "result" in payload and "tenant_id" not in payload:
        return payload["result"]
    return payload


class McpCaseStatusStore:
    """Async-only, write-only from the case workflow's side.

    No ``get_async`` here: the case workflow never needs to read its own
    status back from this store — it always has the freshest state in hand
    already. Reading ``case_status`` back is the sponsor liaison's job
    (``sponsor_tools.py``), a different process on a different trust
    boundary, not this client.
    """

    async def upsert_async(self, record: CaseStatusRecord) -> CaseStatusRecord:
        payload = await call_case_status_tool(
            CASE_STATUS_UPSERT,
            {
                "tenant_id": record.tenant_id,
                "batch_id": record.batch_id,
                "case_id": record.case_id,
                "status": record.status.value,
                "sterility_due_at": (
                    record.sterility_due_at.isoformat()
                    if record.sterility_due_at
                    else None
                ),
                "qp_confirmed_at": (
                    record.qp_confirmed_at.isoformat()
                    if record.qp_confirmed_at
                    else None
                ),
                "updated_at": record.updated_at.isoformat(),
                "case_tenant_id": record.tenant_id,
            },
        )
        return CaseStatusRecord.model_validate(_unwrap(payload))
