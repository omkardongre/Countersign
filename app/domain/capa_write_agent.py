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

"""``capa_write_agent``: the fleet member that writes CAPA records back to
ERPNext after the QP has acted and the investigation report has been filed.

Runs after ``file_qms_report`` (the graph's previous last node). The Non
Conformance description and Quality Action resolution are pre-built
deterministic text (``app.domain.capa_report``) — the model never drafts
CAPA content, it only decides to call the two tenant/case-locked tool
closures bound to it. Same discipline as ``qa_qms_write_agent``: the tools
are case-locked closures with no model-controlled arguments, so the agent
cannot create a CAPA for a different tenant or case than the one this node
was invoked for.

**Failure isolation:** a CAPA write failure is a visible typed error on the
returned ``CapaWriteResult``, never a silent omission. But it does **not**
block the workflow from completing — the QP decision and the filed report
are already done by the time this node runs. The ``QpReviewedPackage``
passes through unchanged, and the ``CapaWriteResult`` is stored in session
state under ``CAPA_RESULT_STATE_KEY`` for the UI/audit trail to read. This
ensures the existing graph output contract is preserved.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import Agent
from google.adk.agents.context import Context
from google.adk.models import Gemini
from google.adk.tools import FunctionTool
from google.adk.workflow import node
from google.genai import types
from pydantic import BaseModel, Field

from app.domain.capa_models import (
    CapaActionType,
    CapaWriteResult,
    NonConformanceWriteResult,
    QualityActionWriteResult,
)
from app.domain.capa_report import (
    build_nonconformance_details,
    build_nonconformance_subject,
    build_quality_action_problem,
    build_quality_action_resolution,
)
from app.domain.qp_models import QpReviewedPackage
from app.mcp.client import call_capa_write_tool

CAPA_WRITE_AGENT_NAME = "capa_write_agent"
CAPA_WRITE_MODEL = "gemini-3.5-flash-lite"
CAPA_RESULT_STATE_KEY = "capa_write_result"

_CAPA_WRITE_INSTRUCTION = """\
You are the CAPA filing agent for one GMP deviation case.

Call `create_non_conformance` exactly once, then call \
`create_quality_action` exactly once with action_type "Corrective", then \
call `create_quality_action` exactly once with action_type "Preventive". \
You have no other tools. The description and resolution text are already \
prepared for you — you do not draft, edit, or summarize CAPA content. \
After each call, confirm that the record was created or re-used.
"""


class CapaWriteAgentSummary(BaseModel):
    """The agent's own account of what it filed. Not used as evidence —
    the returned ``CapaWriteResult`` (captured via the tool closures) is.
    """

    non_conformance_filed: bool = False
    corrective_action_filed: bool = False
    preventive_action_filed: bool = False
    notes: str = Field(default="")


def _make_nonconformance_tool(
    *, tenant_id: str, case_id: str, batch_id: str,
    subject: str, details: str,
    captured: dict[str, Any],
) -> Any:
    """Tenant/case-locked closure: the model supplies no arguments."""

    async def create_non_conformance() -> dict[str, Any]:
        """Create the Non Conformance in ERPNext for this case."""
        payload = await call_capa_write_tool(
            "erpnext_nonconformance_write",
            {
                "tenant_id": tenant_id,
                "case_id": case_id,
                "batch_id": batch_id,
                "subject": subject,
                "details": details,
                "case_tenant_id": tenant_id,
            },
        )
        captured["non_conformance"] = NonConformanceWriteResult.model_validate(payload)
        return {
            "non_conformance_id": payload.get("non_conformance_id"),
            "was_existing": payload.get("was_existing"),
        }

    return create_non_conformance


def _make_quality_action_tool(
    *, tenant_id: str, case_id: str, batch_id: str,
    non_conformance_id: str, action_type: str,
    problem: str, resolution: str,
    captured: dict[str, Any],
) -> Any:
    """Tenant/case-locked closure for one Quality Action."""

    async def create_quality_action() -> dict[str, Any]:
        """Create one Quality Action in ERPNext linked to the NC."""
        payload = await call_capa_write_tool(
            "erpnext_quality_action_write",
            {
                "tenant_id": tenant_id,
                "case_id": case_id,
                "batch_id": batch_id,
                "non_conformance_id": non_conformance_id,
                "action_type": action_type,
                "problem": problem,
                "resolution": resolution,
                "case_tenant_id": tenant_id,
            },
        )
        key = f"quality_action_{action_type.lower()}"
        captured[key] = QualityActionWriteResult.model_validate(payload)
        return {
            "quality_action_id": payload.get("quality_action_id"),
            "was_existing": payload.get("was_existing"),
        }

    return create_quality_action


def get_capa_write_agent(tools: list[Any]) -> Agent:
    """Built per call, per case: each tool is a tenant/case-locked closure."""
    return Agent(
        name=CAPA_WRITE_AGENT_NAME,
        model=Gemini(
            model=CAPA_WRITE_MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        mode="single_turn",
        include_contents="none",
        instruction=_CAPA_WRITE_INSTRUCTION,
        tools=[FunctionTool(tool) for tool in tools],
        output_schema=CapaWriteAgentSummary,
    )


@node(name="file_capa", rerun_on_resume=True)
async def file_capa(
    ctx: Context, node_input: QpReviewedPackage
) -> QpReviewedPackage:
    """Creates Non Conformance + Quality Actions in ERPNext, then passes
    the reviewed package through unchanged.

    ``rerun_on_resume=True`` matches ``file_qms_report``: a crash between
    the report filing and this node completing, followed by an ADK resume,
    reruns this node with the same ``QpReviewedPackage``. Both ERPNext
    endpoints are idempotent by ``(tenant_id, case_id)`` /
    ``(tenant_id, case_id, action_type)``, so a rerun is safe.

    A CAPA write failure is recorded as a typed error on the
    ``CapaWriteResult`` stored in session state — it does **not** raise,
    because the QP decision and the filed report are already complete by
    the time this node runs. The workflow output is unchanged.
    """
    reviewed = (
        node_input
        if isinstance(node_input, QpReviewedPackage)
        else QpReviewedPackage.model_validate(node_input)
    )
    classified = reviewed.release_package.investigation.case.classified

    # ERPNext backend selection is owned by the department MCP service, which
    # holds the credential and fails closed when ERPNext is not configured.
    # The Agent Runtime must always call the governed MCP tools rather than
    # inspecting or instantiating a local department source.

    # Build deterministic content from the case aggregate.
    nc_subject = build_nonconformance_subject(reviewed)
    nc_details = build_nonconformance_details(reviewed)
    qa_problem = build_quality_action_problem(reviewed)
    corrective_resolution = build_quality_action_resolution(
        reviewed, action_type=CapaActionType.CORRECTIVE.value
    )
    preventive_resolution = build_quality_action_resolution(
        reviewed, action_type=CapaActionType.PREVENTIVE.value
    )

    captured: dict[str, Any] = {}
    nc_error: str | None = None
    corrective_error: str | None = None
    preventive_error: str | None = None

    # Step 1: Non Conformance
    try:
        nc_tool = _make_nonconformance_tool(
            tenant_id=classified.tenant_id,
            case_id=classified.case_id,
            batch_id=classified.batch_id,
            subject=nc_subject,
            details=nc_details,
            captured=captured,
        )
        nc_result = await nc_tool()
        nc_id = nc_result["non_conformance_id"]
    except Exception as exc:
        nc_error = str(exc)
        nc_id = None

    # Step 2 + 3: Corrective and Preventive Quality Actions
    # These depend on the NC existing, so skip if NC failed.
    if nc_id is not None:
        for action_type, resolution in (
            (CapaActionType.CORRECTIVE.value, corrective_resolution),
            (CapaActionType.PREVENTIVE.value, preventive_resolution),
        ):
            try:
                qa_tool = _make_quality_action_tool(
                    tenant_id=classified.tenant_id,
                    case_id=classified.case_id,
                    batch_id=classified.batch_id,
                    non_conformance_id=nc_id,
                    action_type=action_type,
                    problem=qa_problem,
                    resolution=resolution,
                    captured=captured,
                )
                await qa_tool()
            except Exception as exc:
                if action_type == CapaActionType.CORRECTIVE.value:
                    corrective_error = str(exc)
                else:
                    preventive_error = str(exc)

    # Assemble the result and store it in session state.
    nc_result_obj: NonConformanceWriteResult | None = captured.get("non_conformance")
    corrective_result_obj: QualityActionWriteResult | None = captured.get(
        "quality_action_corrective"
    )

    capa_result = CapaWriteResult(
        non_conformance=nc_result_obj,
        quality_action=corrective_result_obj,
        non_conformance_error=nc_error,
        quality_action_error=corrective_error or preventive_error,
    )
    ctx.state[CAPA_RESULT_STATE_KEY] = capa_result.model_dump(mode="json")

    # The workflow output is unchanged — QpReviewedPackage passes through.
    return reviewed
