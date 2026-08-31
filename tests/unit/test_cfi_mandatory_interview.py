"""Focused checks for Item 16's deterministic GUI-to-CFI interview route."""

import contextlib
import json

from app.domain.case_models import CaseComplianceResult
from app.domain.cfi_graph import _required_first_enquiry_state
from app.domain.cfi_models import (
    CfiBudgetState,
    CfiEnquiryKind,
    CfiInvestigation,
    CfiRoundState,
    CfiTriggerReason,
)
from app.domain.cfi_trigger import decide_cfi_required
from app.domain.compliance_models import ComplianceStatus
from app.domain.intake import classify_intake
from app.domain.investigation_models import (
    CitedClaim,
    InvestigationCaseResult,
    InvestigationDraft,
    InvestigationGateResult,
    RootCauseHypothesis,
)
from app.domain.master_data import CfiTriggerSpec, RootCauseCategory
from app.domain.models import (
    CaseIntake,
    ClassifiedCase,
    DeviationSeverity,
    DocType,
    LifecycleState,
)


def _spec() -> CfiTriggerSpec:
    return CfiTriggerSpec(
        spec_id="cfi-trigger-test",
        spec_version="test-1",
        spec_sha256="test-sha",
        effective_date="2026-08-31",
        approved_by="QA",
        change_control_ref="CC-test",
        description="test policy",
        low_confidence_threshold=0.6,
        recurrence_prior_occurrences_threshold=3,
        mandatory_operator_interview_document_type="operator_report",
        mandatory_operator_interview_requires_patient_safety_impact=True,
    )


def _case(*, doc_type: DocType, patient_safety: bool, recurrence: int) -> InvestigationCaseResult:
    classified = ClassifiedCase(
        case_id="case-1",
        tenant_id="ACME01",
        batch_id="BATCH-2026-001",
        product_name="AXR450",
        doc_type=doc_type,
        severity=DeviationSeverity.CRITICAL,
        lifecycle_state=LifecycleState.CLASSIFIED,
        sterility_hold_seconds=120,
        patient_safety_impact=patient_safety,
        prior_occurrences_90d=recurrence,
        rationale="test",
    )
    case = CaseComplianceResult(
        classified=classified,
        aggregated_status=ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION,
    )
    return InvestigationCaseResult(
        case=case,
        draft=InvestigationDraft(
            tenant_id=classified.tenant_id,
            case_id=classified.case_id,
            batch_id=classified.batch_id,
            hypotheses=[
                RootCauseHypothesis(
                    category=RootCauseCategory.PERSONNEL,
                    statement="Operator sequence requires review.",
                    supporting_claims=[
                        CitedClaim(
                            claim="The controlled BMR is on the case.",
                            source_uris=["drive://controlled/bmr/page-1"],
                        )
                    ],
                    confidence=0.9,
                )
            ],
        ),
        gate=InvestigationGateResult(passed=True),
    )


def test_qualifying_structured_intake_requires_an_interview_first() -> None:
    decision = decide_cfi_required(
        _case(
            doc_type=DocType.OPERATOR_REPORT,
            patient_safety=True,
            recurrence=3,
        ),
        spec=_spec(),
    )

    assert decision.required is True
    assert decision.required_first_enquiry is CfiEnquiryKind.INTERVIEW
    assert decision.reasons[-1].value == "mandatory_operator_interview"


def test_missing_any_qualifying_signal_does_not_force_the_interview() -> None:
    for case in (
        _case(doc_type=DocType.BATCH_RECORD, patient_safety=True, recurrence=3),
        _case(doc_type=DocType.OPERATOR_REPORT, patient_safety=False, recurrence=3),
        _case(doc_type=DocType.OPERATOR_REPORT, patient_safety=True, recurrence=2),
    ):
        decision = decide_cfi_required(case, spec=_spec())
        assert decision.required is False
        assert decision.required_first_enquiry is None


def test_intake_carries_structured_policy_inputs_to_classified_case() -> None:
    classified = classify_intake(
        CaseIntake(
            tenant_id="ACME01",
            batch_id="BATCH-2026-001",
            product_name="AXR450",
            doc_type=DocType.OPERATOR_REPORT,
            description="Human text must not control the policy.",
            reported_by="QA",
            patient_safety_impact=True,
            prior_occurrences_90d=3,
            sterility_hold_seconds=120,
        )
    )

    assert classified.patient_safety_impact is True
    assert classified.prior_occurrences_90d == 3


def test_required_first_interview_bypasses_the_lead_agent_once() -> None:
    investigation = CfiInvestigation(
        tenant_id="ACME01",
        source_case_id="case-1",
        batch_id="BATCH-2026-001",
        trigger_reasons=[CfiTriggerReason.MANDATORY_OPERATOR_INTERVIEW],
        trigger_spec_version="test-1",
        trigger_spec_sha256="test-sha",
        required_first_enquiry=CfiEnquiryKind.INTERVIEW,
        budget=CfiBudgetState(
            budget_spec_version="budget-1", budget_spec_sha256="budget-sha"
        ),
    )
    state = CfiRoundState(investigation=investigation, round_number=1)

    forced = _required_first_enquiry_state(state)

    assert forced is not None
    assert forced.should_dispatch is True
    assert forced.enquiry_kind is CfiEnquiryKind.INTERVIEW
    assert forced.active_hypothesis_id == "h1"
    assert forced.investigation.hypotheses[0].category is RootCauseCategory.PERSONNEL
    assert _required_first_enquiry_state(forced) is None


def test_launch_acknowledgement_preserves_runtime_revision(monkeypatch) -> None:
    from app.domain import cfi_store_postgres as postgres
    from app.domain.cfi_models import CfiLaunchStatus

    investigation = CfiInvestigation(
        revision=1,
        tenant_id="ACME01",
        source_case_id="case-1",
        batch_id="BATCH-2026-001",
        trigger_reasons=[CfiTriggerReason.MANDATORY_OPERATOR_INTERVIEW],
        trigger_spec_version="test-1",
        trigger_spec_sha256="test-sha",
        required_first_enquiry=CfiEnquiryKind.INTERVIEW,
        budget=CfiBudgetState(
            budget_spec_version="budget-1", budget_spec_sha256="budget-sha"
        ),
        launch_status=CfiLaunchStatus.LAUNCHING,
        launch_attempts=1,
    )

    class Cursor:
        def __init__(self) -> None:
            self.saved_payload = None
            self.last_sql = ""

        def execute(self, sql, params=None) -> None:
            self.last_sql = sql
            if "UPDATE cfi_investigation" in sql:
                self.saved_payload = json.loads(params[0])
                assert "revision = revision + 1" not in sql

        def fetchone(self):
            if self.saved_payload is None:
                return (investigation.model_dump(mode="json"),)
            return (self.saved_payload,)

    cursor = Cursor()

    class Connection:
        def cursor(self):
            return cursor

    @contextlib.contextmanager
    def connect():
        yield Connection()

    monkeypatch.setattr(postgres, "_connect", connect)
    result = postgres.PostgresCfiStore().mark_launch_started(
        tenant_id="ACME01", cfi_id=investigation.cfi_id, invocation_id="inv-1"
    )

    assert result.launch_status is CfiLaunchStatus.STARTED
    assert cursor.saved_payload["revision"] == 1
