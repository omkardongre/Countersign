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

"""Tests for QP confirmation recording (no certification, no release)."""

from app.domain.case_graph import CASE_WORKFLOW_NAME
from app.domain.case_models import CaseComplianceResult
from app.domain.compliance_models import ComplianceStatus
from app.domain.department_models import BranchOutcome, BranchStatus, Department
from app.domain.investigation_models import (
    InvestigationCaseResult,
    InvestigationGateResult,
)
from app.domain.models import (
    ClassifiedCase,
    DeviationSeverity,
    DocType,
    LifecycleState,
)
from app.domain.qp_confirmation import (
    build_qp_review_brief,
    confirm_qp,
    record_qp_decision,
)
from app.domain.qp_models import (
    QpConfirmationAction,
    QpConfirmationOutcome,
    QpConfirmationRequest,
)
from app.domain.release_gate import evaluate_annex16_release_gate
from app.domain.release_models import CaseReleasePackage, ReleaseGateStatus


def test_confirm_qp_reruns_on_resume() -> None:
    assert confirm_qp.rerun_on_resume is True
    assert confirm_qp.name == "confirm_qp"


def _release_package() -> CaseReleasePackage:
    classified = ClassifiedCase(
        tenant_id="aurex",
        batch_id="AX-2291",
        product_name="AXR450 Sterile Injectable",
        doc_type=DocType.BATCH_RECORD,
        severity=DeviationSeverity.CRITICAL,
        lifecycle_state=LifecycleState.CLASSIFIED,
        evidence_refs=["drive://synthetic/bmr/AX-2291/page-14"],
        sterility_hold_seconds=14 * 24 * 60 * 60,
        rationale="sterility_assurance_impact",
    )
    case = CaseComplianceResult(
        classified=classified,
        aggregated_status=ComplianceStatus.INSUFFICIENT_EVIDENCE,
        branches=[
            BranchOutcome(
                department=Department.MANUFACTURING,
                status=BranchStatus.OK,
                source_uris=["drive://synthetic/bmr/AX-2291/page-14"],
            )
        ],
        decision_authority="none",
    )
    investigation = InvestigationCaseResult(
        case=case,
        gate=InvestigationGateResult(passed=True, decision_authority="none"),
        decision_authority="none",
    )
    return CaseReleasePackage(
        investigation=investigation,
        release_gate=evaluate_annex16_release_gate(investigation),
        decision_authority="none",
    )


def test_brief_does_not_rewrite_the_gate() -> None:
    package = _release_package()
    brief = build_qp_review_brief(package)
    assert brief.workflow_name == CASE_WORKFLOW_NAME
    assert brief.release_gate_status is ReleaseGateStatus.NOT_READY
    assert brief.blocked_reasons == package.release_gate.blocked_reasons
    assert brief.decision_authority == "none"
    assert brief.spec_sha256 == package.release_gate.spec_sha256


def test_confirm_on_not_ready_is_rejected() -> None:
    package = _release_package()
    record = record_qp_decision(
        package,
        QpConfirmationRequest(
            action=QpConfirmationAction.CONFIRM,
            qp_name="Jane QP",
            rationale="Pack looks complete.",
        ),
    )
    assert record.outcome is QpConfirmationOutcome.REJECTED
    assert record.action is QpConfirmationAction.CONFIRM
    assert record.rejection_reason is not None
    assert record.decision_authority == "none"
    assert record.gate_status is ReleaseGateStatus.NOT_READY


def test_decline_on_not_ready_is_recorded() -> None:
    package = _release_package()
    record = record_qp_decision(
        package,
        QpConfirmationRequest(
            action=QpConfirmationAction.DECLINE,
            qp_name="Jane QP",
            rationale="Investigation is still open.",
        ),
    )
    assert record.outcome is QpConfirmationOutcome.DECLINED
    assert record.rejection_reason is None


def test_confirm_is_allowed_only_when_gate_is_ready() -> None:
    package = _release_package()
    ready = package.model_copy(
        update={
            "release_gate": package.release_gate.model_copy(
                update={
                    "status": ReleaseGateStatus.READY_FOR_QP_REVIEW,
                    "blocked_reasons": [],
                }
            )
        }
    )
    record = record_qp_decision(
        ready,
        QpConfirmationRequest(
            action=QpConfirmationAction.CONFIRM,
            qp_name="Jane QP",
            rationale="Annex 16 items that we can evidence have passed.",
        ),
    )
    assert record.outcome is QpConfirmationOutcome.CONFIRMED
    assert record.rejection_reason is None
    assert record.decision_authority == "none"
