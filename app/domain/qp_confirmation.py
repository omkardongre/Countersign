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

"""QP HITL: pause for structured confirmation, then record the human act.

Uses ``rerun_on_resume=True`` so the Annex 16 pack is recovered as
``node_input`` from the event log, not from client-writable session state.
"""

from __future__ import annotations

import datetime

from google.adk.agents.context import Context
from google.adk.events.request_input import RequestInput
from google.adk.workflow import node

from app.domain.qp_models import (
    QpConfirmationAction,
    QpConfirmationOutcome,
    QpConfirmationRecord,
    QpConfirmationRequest,
    QpReviewBrief,
    QpReviewedPackage,
)
from app.domain.release_models import CaseReleasePackage, ReleaseGateStatus

QP_INTERRUPT_PREFIX = "qp_confirmation"


def _package(node_input: CaseReleasePackage | dict) -> CaseReleasePackage:
    if isinstance(node_input, CaseReleasePackage):
        return node_input
    return CaseReleasePackage.model_validate(node_input)


def _request(node_input: QpConfirmationRequest | dict) -> QpConfirmationRequest:
    if isinstance(node_input, QpConfirmationRequest):
        return node_input
    payload = node_input
    if isinstance(payload, dict) and "action" not in payload and "result" in payload:
        payload = payload["result"]
    return QpConfirmationRequest.model_validate(payload)


def build_qp_review_brief(package: CaseReleasePackage) -> QpReviewBrief:
    classified = package.investigation.case.classified
    return QpReviewBrief(
        tenant_id=classified.tenant_id,
        case_id=classified.case_id,
        batch_id=classified.batch_id,
        release_gate_status=package.release_gate.status,
        blocked_reasons=list(package.release_gate.blocked_reasons),
        spec_id=package.release_gate.spec_id,
        spec_version=package.release_gate.spec_version,
        spec_sha256=package.release_gate.spec_sha256,
        investigation_gate_passed=package.investigation.gate.passed,
        workflow_name="deviation_case",
        decision_authority="none",
    )


def record_qp_decision(
    package: CaseReleasePackage,
    request: QpConfirmationRequest,
    *,
    recorded_at: datetime.datetime | None = None,
) -> QpConfirmationRecord:
    """Map the QP reply onto the gate. Confirm is refused while not_ready."""
    classified = package.investigation.case.classified
    stamp = recorded_at or datetime.datetime.now(datetime.UTC)
    gate_status = package.release_gate.status
    rejection_reason = None
    if request.action is QpConfirmationAction.CONFIRM:
        if gate_status is ReleaseGateStatus.NOT_READY:
            outcome = QpConfirmationOutcome.REJECTED
            rejection_reason = (
                "Confirmation is refused while the Annex 16 pack is not_ready."
            )
        else:
            outcome = QpConfirmationOutcome.CONFIRMED
    elif request.action is QpConfirmationAction.DECLINE:
        outcome = QpConfirmationOutcome.DECLINED
    else:
        outcome = QpConfirmationOutcome.CANNOT_CONFIRM
    return QpConfirmationRecord(
        action=request.action,
        outcome=outcome,
        qp_name=request.qp_name,
        rationale=request.rationale,
        tenant_id=classified.tenant_id,
        case_id=classified.case_id,
        batch_id=classified.batch_id,
        gate_status=gate_status,
        recorded_at=stamp,
        rejection_reason=rejection_reason,
        decision_authority="none",
    )


@node(name="confirm_qp", rerun_on_resume=True)
def confirm_qp(node_input: CaseReleasePackage, ctx: Context):
    """Pause for CDMO QP confirmation, then record it against the recovered pack."""
    package = _package(node_input)
    classified = package.investigation.case.classified
    interrupt_id = f"{QP_INTERRUPT_PREFIX}:{classified.case_id}"
    resume_inputs = ctx.resume_inputs or {}
    resume_input = resume_inputs.get(interrupt_id)
    if not resume_input:
        brief = build_qp_review_brief(package)
        blocked = "; ".join(package.release_gate.blocked_reasons) or "none"
        yield RequestInput(
            interrupt_id=interrupt_id,
            message=(
                "CDMO QP confirmation for tenant "
                f"{classified.tenant_id} case {classified.case_id} batch "
                f"{classified.batch_id}. Gate status is "
                f"{package.release_gate.status.value}. This is confirmation of "
                "manufacturing steps performed on site, not MAH certification or "
                f"batch release. Blocked items: {blocked}"
            ),
            payload=brief.model_dump(mode="json"),
            response_schema=QpConfirmationRequest,
        )
        return
    request = _request(resume_input)
    yield QpReviewedPackage(
        release_package=package,
        qp_confirmation=record_qp_decision(package, request),
        decision_authority="none",
    )
