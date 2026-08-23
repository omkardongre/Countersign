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

"""Sponsor liaison's own MCP call to ``sponsor_batch_status``.

Deliberately does not import ``app.domain.sponsor_tools`` and call it
in-process. The sponsor liaison is agent 8 of 8 — "A2A peer, **separate
service**" per
``final_reseach/10_gap_analysis_and_win_plan.md`` section 4.1 — and reaching
the status store directly would collapse that separation back into a shared
process the same way the case workflow itself never opens Cloud SQL
directly. This client calls the department MCP service the same way the
case workflow's own ``app/mcp/client.py`` does, through
``COUNTERSIGN_MCP_URL``, and is subject to the *sponsor_liaison* Gateway
identity's allow-list (``infra/geap/mcp-sponsor-liaison-status-only.yaml``),
not the case workflow's.
"""

from __future__ import annotations

from app.domain.department_tools import SPONSOR_BATCH_STATUS
from app.domain.sponsor_models import SponsorBatchStatusResult
from app.mcp.client import DepartmentToolCallError, call_sponsor_tool


def _unwrap(payload: dict) -> dict:
    """FastMCP wraps some structured returns under ``{"result": ...}``."""
    if "result" in payload and "tenant_id" not in payload:
        return payload["result"]
    return payload


async def fetch_sponsor_batch_status(
    *, sponsor_id: str, tenant_id: str, batch_id: str
) -> SponsorBatchStatusResult:
    """Call the one tool this service's Gateway identity may reach.

    ``sponsor_id`` here is the already-authenticated caller from
    ``app/sponsor_liaison/auth.py`` — never a value taken from the model's
    own output — so a compromised or confused model in this service can at
    most ask about a batch outside its own tenant and be denied by
    ``sponsor_batch_status``'s own scope check; it can never impersonate a
    different sponsor company.
    """
    try:
        payload = await call_sponsor_tool(
            SPONSOR_BATCH_STATUS,
            {"sponsor_id": sponsor_id, "tenant_id": tenant_id, "batch_id": batch_id},
        )
    except DepartmentToolCallError as exc:
        # The MCP tool raises ToolError -> isError=True for
        # SponsorScopeDenied, which call_mcp_tool re-raises as this type.
        # Surface it as a denial the agent can explain, not a transport
        # failure to retry.
        raise SponsorLiaisonDenied(str(exc)) from exc
    return SponsorBatchStatusResult.model_validate(_unwrap(payload))


class SponsorLiaisonDenied(Exception):
    """The department MCP denied this sponsor/tenant/batch combination."""
