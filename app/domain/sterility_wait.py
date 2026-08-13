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

from google.adk.agents.context import Context
from google.adk.events.request_input import RequestInput
from google.adk.workflow import node

from app.domain.investigation_models import InvestigationCaseResult
from app.domain.sterility_models import (
    SterilityHoldBrief,
    SterilityIncubationStatus,
    SterilityTickRequest,
)
from app.domain.sterility_store import get_sterility_store
from app.domain.workflow_meta import CASE_WORKFLOW_VERSION

STERILITY_INTERRUPT_PREFIX = "sterility_incubation"


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


def build_sterility_hold_brief(
    investigation: InvestigationCaseResult,
    *,
    workflow_version: str,
) -> SterilityHoldBrief:
    classified = investigation.case.classified
    record = get_sterility_store().start(
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
def await_sterility_incubation(
    node_input: InvestigationCaseResult, ctx: Context
):
    """Pause until the incubation store is complete, then pass the case through."""
    investigation = _investigation(node_input)
    classified = investigation.case.classified
    store = get_sterility_store()
    record = store.get(classified.tenant_id, classified.batch_id)
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

    record = store.get(classified.tenant_id, classified.batch_id)
    if record is not None and record.status is SterilityIncubationStatus.COMPLETE:
        yield investigation
        return

    brief = build_sterility_hold_brief(
        investigation, workflow_version=CASE_WORKFLOW_VERSION
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
