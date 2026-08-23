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

"""Sterility incubation hold: checkpoint and exit until external state is complete.

Not a ``LongRunningFunctionTool`` and not an LLM. The store is authoritative.
A scheduler tick writes completion, then ADK resume re-runs this node.
"""

from __future__ import annotations

import datetime
import logging

from google.adk.agents.context import Context
from google.adk.events.request_input import RequestInput
from google.adk.workflow import node
from google.api_core.exceptions import GoogleAPICallError, RetryError
from mcp.shared.exceptions import McpError

from app.domain.case_status_mcp import McpCaseStatusStore
from app.domain.case_status_models import CaseStatus, CaseStatusRecord
from app.domain.investigation_models import InvestigationCaseResult
from app.domain.sterility_models import (
    SterilityHoldBrief,
    SterilityIncubationStatus,
    SterilityResumeHandle,
    SterilityTickRequest,
)
from app.domain.sterility_store_mcp import McpSterilityStore
from app.domain.workflow_meta import CASE_WORKFLOW_VERSION
from app.mcp.client import DepartmentToolCallError

logger = logging.getLogger(__name__)

STERILITY_INTERRUPT_PREFIX = "sterility_incubation"

# Same narrow set case_memory_node.py catches for its own side-channel
# writes: a real outage of the status store, not a bug in this code path.
_STATUS_STORE_OUTAGE_ERRORS = (
    DepartmentToolCallError,
    McpError,
    GoogleAPICallError,
    RetryError,
    ConnectionError,
    TimeoutError,
)


def _investigation(
    node_input: InvestigationCaseResult | dict,
) -> InvestigationCaseResult:
    if isinstance(node_input, InvestigationCaseResult):
        return node_input
    return InvestigationCaseResult.model_validate(node_input)


def _tick(payload: SterilityTickRequest | dict) -> SterilityTickRequest:
    if isinstance(payload, SterilityTickRequest):
        return payload
    body = payload
    if isinstance(body, dict) and "result" in body and "tenant_id" not in body:
        body = body["result"]
    return SterilityTickRequest.model_validate(body)


async def build_sterility_hold_brief(
    investigation: InvestigationCaseResult,
    *,
    workflow_version: str,
    store: McpSterilityStore,
) -> SterilityHoldBrief:
    classified = investigation.case.classified
    record = await store.start_async(
        tenant_id=classified.tenant_id,
        batch_id=classified.batch_id,
        case_id=classified.case_id,
        workflow_version=workflow_version,
    )
    return SterilityHoldBrief(
        tenant_id=record.tenant_id,
        case_id=record.case_id,
        batch_id=record.batch_id,
        status=record.status,
        started_at=record.started_at,
        due_at=record.due_at,
        hold_seconds=record.hold_seconds,
        compressed_for_demo=record.compressed_for_demo,
        workflow_version=record.workflow_version,
        decision_authority="none",
    )


@node(name="await_sterility_incubation", rerun_on_resume=True)
async def await_sterility_incubation(
    node_input: InvestigationCaseResult, ctx: Context
):
    """Pause until the incubation store is complete, then pass the case through.

    The clock is reached over MCP rather than a direct Cloud SQL connection:
    an Agent Identity principal cannot be a Cloud SQL IAM database user, so
    this node has the same credential shape as the QC/supply collectors.
    """
    investigation = _investigation(node_input)
    classified = investigation.case.classified
    store = McpSterilityStore()
    record = await store.get_async(classified.tenant_id, classified.batch_id)
    if record is not None and record.status is SterilityIncubationStatus.COMPLETE:
        yield investigation
        return

    interrupt_id = f"{STERILITY_INTERRUPT_PREFIX}:{classified.case_id}"
    resume_inputs = ctx.resume_inputs or {}
    resume_input = resume_inputs.get(interrupt_id)
    if resume_input:
        tick = _tick(resume_input)
        if (
            tick.tenant_id != classified.tenant_id
            or tick.batch_id != classified.batch_id
        ):
            raise ValueError("Sterility tick scope does not match the classified case.")
        # Store remains authoritative: a tick without a complete row does not advance.

    record = await store.get_async(classified.tenant_id, classified.batch_id)
    if record is not None and record.status is SterilityIncubationStatus.COMPLETE:
        yield investigation
        return

    brief = await build_sterility_hold_brief(
        investigation, workflow_version=CASE_WORKFLOW_VERSION, store=store
    )
    # Persist where this pause lives so a scheduler tick weeks later can
    # resume the same invocation. Rebound on every pause: last checkpoint wins.
    await store.bind_resume_handle_async(
        classified.tenant_id,
        classified.batch_id,
        SterilityResumeHandle(
            app_name=ctx.session.app_name,
            user_id=ctx.user_id,
            session_id=ctx.session.id,
            invocation_id=ctx.invocation_id,
            interrupt_id=interrupt_id,
        ),
    )
    # Batch-keyed status for the sponsor liaison's read boundary
    # (sponsor_tools.py). A store-outage here must not stall the hold the
    # same way a Case Memory outage must not stall the case
    # (case_memory_node.py) — it is a side channel for an external reader,
    # not part of this node's own gate.
    try:
        await McpCaseStatusStore().upsert_async(
            CaseStatusRecord(
                tenant_id=classified.tenant_id,
                batch_id=classified.batch_id,
                case_id=classified.case_id,
                status=CaseStatus.ON_STERILITY_HOLD,
                sterility_due_at=brief.due_at,
                updated_at=datetime.datetime.now(datetime.UTC),
            )
        )
    except _STATUS_STORE_OUTAGE_ERRORS as exc:
        logger.warning(
            "Case status upsert failed on sterility park, continuing: %s", exc
        )
    yield RequestInput(
        interrupt_id=interrupt_id,
        message=(
            "Sterility incubation hold for tenant "
            f"{classified.tenant_id} case {classified.case_id} batch "
            f"{classified.batch_id}. Due {brief.due_at.isoformat()}. "
            "This is not a batch release. Resume after the store is marked complete."
        ),
        payload=brief.model_dump(mode="json"),
        response_schema=SterilityTickRequest,
    )
