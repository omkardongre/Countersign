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

"""Tests for the deterministic Annex 16 section 1.7 confirmation checklist."""

import hashlib
from pathlib import Path

import pytest

from app.domain import release_gate as release_gate_mod
from app.domain.case_models import CaseComplianceResult
from app.domain.compliance_models import (
    ComplianceFinding,
    ComplianceFindingType,
    ComplianceStatus,
)
from app.domain.department_models import BranchOutcome, BranchStatus, Department
from app.domain.investigation_models import (
    InvestigationCaseResult,
    InvestigationGateResult,
)
from app.domain.master_data import RootCauseCategory
from app.domain.models import (
    ClassifiedCase,
    DeviationSeverity,
    DocType,
    LifecycleState,
)
from app.domain.release_gate import (
    Annex16Checklist,
    Annex16ChecklistItem,
    evaluate_annex16_release_gate,
    load_annex16_checklist,
)
from app.domain.release_models import (
    ChecklistItemStatus,
    ReleaseGateResult,
    ReleaseGateStatus,
)

_CHECKLIST_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "domain"
    / "master_data"
    / "annex16_checklist_v1.json"
)


def _case(
    *,
    manufacturing_status: BranchStatus = BranchStatus.OK,
    qc_status: BranchStatus = BranchStatus.OK,
    supply_status: BranchStatus = BranchStatus.OK,
    manufacturing_findings: list[ComplianceFinding] | None = None,
    qc_findings: list[ComplianceFinding] | None = None,
    supply_findings: list[ComplianceFinding] | None = None,
) -> CaseComplianceResult:
    classified = ClassifiedCase(
        tenant_id="aurex",
        batch_id="AX-2291",
        product_name="AXR450 Sterile Injectable",
        doc_type=DocType.BATCH_RECORD,
        severity=DeviationSeverity.CRITICAL,
        lifecycle_state=LifecycleState.CLASSIFIED,
        evidence_refs=["drive://synthetic/bmr/AX-2291/page-14"],
        sterility_hold_days=14,
        rationale="sterility_assurance_impact",
    )
    branches = [
        BranchOutcome(
            department=Department.MANUFACTURING,
            status=manufacturing_status,
            source_uris=["drive://synthetic/bmr/AX-2291/page-14"],
            findings=manufacturing_findings or [],
            error=(
                "not an accepted evidence type"
                if manufacturing_status is BranchStatus.FAILED
                else None
            ),
        ),
        BranchOutcome(
            department=Department.QC,
            status=qc_status,
            source_uris=["cloudsql://aurex/lims/AX-2291"],
            findings=qc_findings or [],
        ),
        BranchOutcome(
            department=Department.SUPPLY,
            status=supply_status,
            source_uris=["drive://synthetic/coa/AX-2291"],
            findings=supply_findings or [],
        ),
    ]
    return CaseComplianceResult(
        classified=classified,
        aggregated_status=ComplianceStatus.INSUFFICIENT_EVIDENCE,
        branches=branches,
        findings=[finding for branch in branches for finding in branch.findings],
        decision_authority="none",
    )


def _package(case: CaseComplianceResult) -> InvestigationCaseResult:
    return InvestigationCaseResult(
        case=case,
        gate=InvestigationGateResult(passed=True, decision_authority="none"),
        decision_authority="none",
    )


def _item(gate: ReleaseGateResult, annex_ref: str):
    return next(item for item in gate.items if item.annex_ref == annex_ref)


def _finding(
    *,
    finding_type: ComplianceFindingType,
    category: RootCauseCategory,
    field_name: str = "ASSAY",
) -> ComplianceFinding:
    return ComplianceFinding(
        finding_type=finding_type,
        field_name=field_name,
        label=field_name,
        root_cause_category=category,
        description="synthetic finding",
    )


def test_checklist_is_hashed_master_data() -> None:
    checklist = load_annex16_checklist()
    raw = _CHECKLIST_PATH.read_bytes()
    assert checklist.spec_id == "annex16_qp_checklist"
    assert checklist.spec_sha256 == hashlib.sha256(raw).hexdigest()
    assert len(checklist.items) == 21
    bound = {item.annex_ref: item.evaluator for item in checklist.items}
    assert bound["1.7.6"] == "materials_gating"
    assert bound["1.7.11"] == "records_complete"
    assert bound["1.7.13"] == "finished_product_qc"
    assert bound["1.7.16"] == "investigations_complete"
    assert bound["1.7.17"] == "open_batch_issues"
    assert bound["1.7.18"] == "technical_agreement"


def test_happy_path_is_not_ready_because_investigation_is_open() -> None:
    gate = evaluate_annex16_release_gate(_package(_case()))
    assert gate.status is ReleaseGateStatus.NOT_READY
    assert gate.decision_authority == "none"
    assert _item(gate, "1.7.11").status is ChecklistItemStatus.PASS
    assert _item(gate, "1.7.13").status is ChecklistItemStatus.PASS
    assert _item(gate, "1.7.6").status is ChecklistItemStatus.PASS
    assert _item(gate, "1.7.16").status is ChecklistItemStatus.FAIL
    assert _item(gate, "1.7.17").status is ChecklistItemStatus.FAIL
    assert _item(gate, "1.7.18").status is ChecklistItemStatus.NOT_EVALUATED
    assert _item(gate, "1.7.1").status is ChecklistItemStatus.NOT_EVALUATED
    blocked = " ".join(gate.blocked_reasons)
    assert "1.7.16" in blocked
    assert "1.7.17" in blocked
    assert "1.7.18" in blocked


def test_failed_manufacturing_fails_records_complete() -> None:
    gate = evaluate_annex16_release_gate(
        _package(_case(manufacturing_status=BranchStatus.FAILED))
    )
    assert _item(gate, "1.7.11").status is ChecklistItemStatus.FAIL
    assert _item(gate, "1.7.13").status is ChecklistItemStatus.PASS
    assert _item(gate, "1.7.6").status is ChecklistItemStatus.PASS
    assert gate.status is ReleaseGateStatus.NOT_READY


def test_completeness_finding_fails_records_complete() -> None:
    gate = evaluate_annex16_release_gate(
        _package(
            _case(
                manufacturing_findings=[
                    _finding(
                        finding_type=ComplianceFindingType.COMPLETENESS,
                        category=RootCauseCategory.UNCLASSIFIED,
                        field_name="operator_signoff",
                    )
                ]
            )
        )
    )
    assert _item(gate, "1.7.11").status is ChecklistItemStatus.FAIL


def test_lims_oos_fails_finished_product_qc() -> None:
    gate = evaluate_annex16_release_gate(
        _package(
            _case(
                qc_findings=[
                    _finding(
                        finding_type=ComplianceFindingType.OUT_OF_SPECIFICATION,
                        category=RootCauseCategory.MEASUREMENT,
                    )
                ]
            )
        )
    )
    assert _item(gate, "1.7.13").status is ChecklistItemStatus.FAIL
    assert _item(gate, "1.7.11").status is ChecklistItemStatus.PASS


def test_supply_findings_fail_materials_gating() -> None:
    gate = evaluate_annex16_release_gate(
        _package(
            _case(
                supply_findings=[
                    _finding(
                        finding_type=ComplianceFindingType.COMPLETENESS,
                        category=RootCauseCategory.MATERIAL,
                        field_name="coa_signature",
                    )
                ]
            )
        )
    )
    assert _item(gate, "1.7.6").status is ChecklistItemStatus.FAIL
    assert _item(gate, "1.7.11").status is ChecklistItemStatus.PASS


def test_unknown_evaluator_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Annex16Checklist(
        spec_id="annex16_qp_checklist",
        spec_version="test",
        spec_sha256="0" * 64,
        items=[
            Annex16ChecklistItem(
                annex_ref="1.7.99",
                title="Unknown binding",
                evaluator="not_a_real_evaluator",
                required_for_confirmation=True,
            )
        ],
    )
    monkeypatch.setattr(release_gate_mod, "load_annex16_checklist", lambda: fake)
    gate = evaluate_annex16_release_gate(_package(_case()))
    assert _item(gate, "1.7.99").status is ChecklistItemStatus.FAIL
    assert gate.status is ReleaseGateStatus.NOT_READY


def test_release_gate_cannot_claim_decision_authority() -> None:
    with pytest.raises(ValueError, match="cannot claim decision authority"):
        ReleaseGateResult(
            status=ReleaseGateStatus.NOT_READY,
            spec_id="annex16_qp_checklist",
            spec_version="1.0.0",
            spec_sha256="0" * 64,
            items=[],
            decision_authority="qp",
        )
