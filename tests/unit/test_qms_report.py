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

"""Deterministic report content builder. No Docs/Drive dependency here —
those live behind the MCP tool and are exercised separately."""

from __future__ import annotations

import datetime

from app.domain.case_models import CaseComplianceResult
from app.domain.compliance_models import ComplianceStatus
from app.domain.department_models import BranchOutcome, BranchStatus, Department
from app.domain.intake import classify_intake
from app.domain.investigation_models import (
    ChallengerAttack,
    ChallengerAttackType,
    ChallengerReport,
    CitedClaim,
    InvestigationCaseResult,
    InvestigationDraft,
    InvestigationGateResult,
    ProductImpactDraft,
    RootCauseHypothesis,
)
from app.domain.master_data import RootCauseCategory
from app.domain.models import CaseIntake, DocType
from app.domain.qms_report import build_report_body, build_report_title
from app.domain.qp_models import (
    QpConfirmationAction,
    QpConfirmationOutcome,
    QpConfirmationRecord,
    QpReviewedPackage,
)
from app.domain.release_models import (
    CaseReleasePackage,
    ChecklistItemResult,
    ChecklistItemStatus,
    ReleaseGateResult,
    ReleaseGateStatus,
)


def _classified_case() -> CaseComplianceResult:
    classified = classify_intake(
        CaseIntake(
            tenant_id="aurex",
            batch_id="AX-2291",
            product_name="AXR450 Sterile Injectable",
            doc_type=DocType.BATCH_RECORD,
            description="Test deviation for report builder.",
            reported_by="Quality Control",
            sterility_assurance_impact=True,
            sterility_hold_seconds=14 * 24 * 60 * 60,
        )
    )
    branch = BranchOutcome(
        department=Department.MANUFACTURING,
        status=BranchStatus.OK,
        source_uris=["drive://file123"],
    )
    return CaseComplianceResult(
        classified=classified,
        aggregated_status=ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION,
        branches=[branch],
    )


def _reviewed_package() -> QpReviewedPackage:
    case = _classified_case()
    draft = InvestigationDraft(
        tenant_id=case.classified.tenant_id,
        case_id=case.classified.case_id,
        batch_id=case.classified.batch_id,
        hypotheses=[
            RootCauseHypothesis(
                category=RootCauseCategory.MEASUREMENT,
                statement="Calibration drift on mixer sensor.",
                supporting_claims=[
                    CitedClaim(
                        claim="Sensor reading drifted 0.4C over the run.",
                        source_uris=["drive://file123"],
                    )
                ],
                confidence=0.7,
            )
        ],
        product_impact=ProductImpactDraft(
            statement="No confirmed patient-safety impact identified.",
            supporting_claims=[
                CitedClaim(claim="No OOS finding on final QC.", source_uris=["drive://file123"])
            ],
            patient_safety_touch=False,
        ),
        open_questions=["Confirm calibration log for the prior shift."],
    )
    challenger = ChallengerReport(
        attacks=[
            ChallengerAttack(
                attack_type=ChallengerAttackType.ALTERNATIVE_CAUSE,
                target="Calibration drift hypothesis",
                detail="Ambient temperature swing was not ruled out.",
            )
        ]
    )
    investigation = InvestigationCaseResult(
        case=case,
        draft=draft,
        challenger=challenger,
        gate=InvestigationGateResult(passed=True),
    )
    release_gate = ReleaseGateResult(
        status=ReleaseGateStatus.READY_FOR_QP_REVIEW,
        spec_id="annex16_v1",
        spec_version="1",
        spec_sha256="a" * 64,
        items=[
            ChecklistItemResult(
                annex_ref="1.7.1",
                title="Records complete",
                status=ChecklistItemStatus.PASS,
                evidence_uris=["drive://file123"],
                reason="Manufacturing branch collected; no completeness findings.",
                required_for_confirmation=True,
            )
        ],
    )
    package = CaseReleasePackage(investigation=investigation, release_gate=release_gate)
    confirmation = QpConfirmationRecord(
        action=QpConfirmationAction.CONFIRM,
        outcome=QpConfirmationOutcome.CONFIRMED,
        qp_name="Jane QP",
        rationale="All checklist items pass; investigation citations verified.",
        tenant_id=case.classified.tenant_id,
        case_id=case.classified.case_id,
        batch_id=case.classified.batch_id,
        gate_status=ReleaseGateStatus.READY_FOR_QP_REVIEW,
        recorded_at=datetime.datetime(2026, 8, 24, tzinfo=datetime.UTC),
    )
    return QpReviewedPackage(release_package=package, qp_confirmation=confirmation)


def test_build_report_title_names_tenant_case_and_batch() -> None:
    reviewed = _reviewed_package()
    title = build_report_title(reviewed)
    classified = reviewed.release_package.investigation.case.classified
    assert classified.tenant_id in title
    assert classified.case_id in title
    assert classified.batch_id in title


def test_build_report_body_includes_every_section() -> None:
    body = build_report_body(_reviewed_package())
    for header in (
        "SECTION 1: CASE IDENTITY",
        "SECTION 2: DEPARTMENT EVIDENCE",
        "SECTION 3: INVESTIGATION",
        "SECTION 4: CHALLENGER REVIEW",
        "SECTION 5: ANNEX 16 SECTION 1.7 CONFIRMATION CHECKLIST",
        "SECTION 6: QP DECISION RECORD",
    ):
        assert header in body


def test_build_report_body_cites_evidence_sources() -> None:
    body = build_report_body(_reviewed_package())
    assert "drive://file123" in body
    assert "Sensor reading drifted 0.4C over the run." in body


def test_build_report_body_states_no_decision_authority() -> None:
    body = build_report_body(_reviewed_package())
    assert "decision_authority: none" in body


def test_build_report_body_records_qp_decision() -> None:
    body = build_report_body(_reviewed_package())
    assert "Jane QP" in body
    assert "confirmed" in body
    assert "confirm" in body
