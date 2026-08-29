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

"""Tests for the deterministic investigation citation gate."""

from app.domain.case_models import CaseComplianceResult
from app.domain.compliance_models import ComplianceStatus
from app.domain.department_models import BranchOutcome, BranchStatus, Department
from app.domain.investigation_gate import evaluate_investigation_gate
from app.domain.investigation_models import (
    ChallengedInvestigation,
    ChallengerAttack,
    ChallengerAttackType,
    ChallengerReport,
    CitedClaim,
    InvestigationDraft,
    ProductImpactDraft,
    RootCauseHypothesis,
)
from app.domain.master_data import RootCauseCategory
from app.domain.models import (
    ClassifiedCase,
    DeviationSeverity,
    DocType,
    LifecycleState,
)


def _case(*, status: ComplianceStatus = ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION):
    classified = ClassifiedCase(
        tenant_id="aurex",
        batch_id="AX-2291",
        product_name="AXR450 Sterile Injectable",
        doc_type=DocType.BATCH_RECORD,
        severity=DeviationSeverity.CRITICAL,
        lifecycle_state=LifecycleState.CLASSIFIED,
        sterility_hold_seconds=14 * 24 * 60 * 60,
        rationale="sterility_assurance_impact",
    )
    branches = [
        BranchOutcome(
            department=Department.MANUFACTURING,
            status=BranchStatus.OK,
            source_uris=["drive://synthetic/bmr/AX-2291/page-14"],
        ),
        BranchOutcome(
            department=Department.QC,
            status=BranchStatus.OK,
            source_uris=["cloudsql://aurex/lims/AX-2291"],
        ),
        BranchOutcome(
            department=Department.SUPPLY,
            status=BranchStatus.OK,
            source_uris=["drive://synthetic/coa/AX-2291"],
        ),
    ]
    return CaseComplianceResult(
        classified=classified,
        aggregated_status=status,
        branches=branches,
        decision_authority="none",
    )


def _claim(uri: str = "drive://synthetic/bmr/AX-2291/page-14") -> CitedClaim:
    return CitedClaim(claim="BMR page is on the case.", source_uris=[uri])


def _draft(case: CaseComplianceResult, uri: str | None = None) -> InvestigationDraft:
    return InvestigationDraft(
        tenant_id=case.classified.tenant_id,
        case_id=case.classified.case_id,
        batch_id=case.classified.batch_id,
        hypotheses=[
            RootCauseHypothesis(
                category=RootCauseCategory.METHOD,
                statement="Blend-speed excursion is the leading process deviation.",
                supporting_claims=[_claim(uri or "drive://synthetic/bmr/AX-2291/page-14")],
                confidence=0.6,
            )
        ],
        product_impact=ProductImpactDraft(
            statement="Impact remains unassigned pending QP review.",
            supporting_claims=[_claim(uri or "drive://synthetic/bmr/AX-2291/page-14")],
        ),
        decision_authority="none",
    )


def test_gate_passes_when_every_claim_is_on_the_case() -> None:
    case = _case()
    challenged = ChallengedInvestigation(
        case=case,
        draft=_draft(case),
        challenger=ChallengerReport(attacks=[]),
    )
    gate = evaluate_investigation_gate(challenged)
    assert gate.passed is True
    assert gate.blocked_reasons == []
    assert gate.decision_authority == "none"


def test_gate_blocks_unknown_source_uri() -> None:
    case = _case()
    challenged = ChallengedInvestigation(
        case=case,
        draft=_draft(case, uri="https://example.invalid/invented"),
        challenger=ChallengerReport(attacks=[]),
    )
    gate = evaluate_investigation_gate(challenged)
    assert gate.passed is False
    assert any("unknown source_uri" in reason for reason in gate.blocked_reasons)


def test_gate_does_not_let_challenger_veto_cited_draft() -> None:
    case = _case()
    challenged = ChallengedInvestigation(
        case=case,
        draft=_draft(case),
        challenger=ChallengerReport(
            attacks=[
                ChallengerAttack(
                    attack_type=ChallengerAttackType.UNSOURCED_CLAIM,
                    target="hypotheses[0]",
                    detail="Statement is not tied to a listed source.",
                )
            ]
        ),
    )
    gate = evaluate_investigation_gate(challenged)
    assert gate.passed is True


def test_gate_runs_when_aggregated_status_is_insufficient_but_branches_ok() -> None:
    case = _case(status=ComplianceStatus.INSUFFICIENT_EVIDENCE)
    challenged = ChallengedInvestigation(
        case=case,
        draft=_draft(case),
        challenger=ChallengerReport(attacks=[]),
    )
    gate = evaluate_investigation_gate(challenged)
    assert gate.passed is True


def test_gate_blocks_skipped_investigation() -> None:
    case = _case(status=ComplianceStatus.INSUFFICIENT_EVIDENCE)
    challenged = ChallengedInvestigation(
        case=case,
        skipped=True,
        skip_reason="One or more department branches failed.",
        draft=None,
        challenger=None,
    )
    gate = evaluate_investigation_gate(challenged)
    assert gate.passed is False
    assert "failed" in (gate.blocked_reasons[0] if gate.blocked_reasons else "")
