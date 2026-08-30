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

"""Unit tests for the ERPNext CAPA write-back: models, report builder,
source write methods, MCP tool functions, and the ``file_capa`` graph node.

Same injection pattern as ``test_department_source_erpnext.py``: the
``request_json`` callable is replaced with a stub so no real HTTP call is
made, and credentials are loaded from an env var.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

import pytest

from app.domain.capa_models import (
    CapaActionType,
    CapaWriteResult,
    NonConformanceWriteRequest,
    NonConformanceWriteResult,
    QualityActionWriteRequest,
    QualityActionWriteResult,
)
from app.domain.capa_report import (
    build_nonconformance_details,
    build_nonconformance_subject,
    build_quality_action_problem,
    build_quality_action_resolution,
)
from app.domain.department_source import (
    BACKEND_ERPNEXT,
    reset_department_source_cache,
)
from app.domain.department_source_erpnext import (
    ERPNextApiError,
    ErpNextSupplySource,
    reset_erpnext_credentials_cache,
)
from app.domain.department_tools import (
    CAPA_WRITE_TOOLS,
    ERPNEXT_NONCONFORMANCE_WRITE,
    ERPNEXT_QUALITY_ACTION_WRITE,
    CapaTenantMismatch,
    ErpNextCapaWriteError,
    erpnext_nonconformance_write,
    erpnext_quality_action_write,
)
from app.domain.qp_models import QpReviewedPackage


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reset_department_source_cache()
    reset_erpnext_credentials_cache()
    yield
    reset_department_source_cache()
    reset_erpnext_credentials_cache()


def _credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "COUNTERSIGN_ERPNEXT_CREDENTIALS_JSON",
        json.dumps(
            {
                "ACME01": {
                    "base_url": "https://erp.example.test",
                    "api_key": "integration-key",
                    "api_secret": "integration-secret",
                }
            }
        ),
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_nonconformance_result_rejects_decision_authority() -> None:
    with pytest.raises(ValueError, match="decision authority"):
        NonConformanceWriteResult(
            non_conformance_id="NC-001",
            non_conformance_url="https://erp.example.test/app/non-conformance/NC-001",
            was_existing=False,
            decision_authority="qp",
        )


def test_quality_action_result_rejects_decision_authority() -> None:
    with pytest.raises(ValueError, match="decision authority"):
        QualityActionWriteResult(
            quality_action_id="QA-001",
            quality_action_url="https://erp.example.test/app/quality-action/QA-001",
            was_existing=False,
            decision_authority="qp",
        )


def test_capa_write_result_rejects_decision_authority() -> None:
    with pytest.raises(ValueError, match="decision authority"):
        CapaWriteResult(decision_authority="qp")


def test_capa_action_type_values() -> None:
    assert CapaActionType.CORRECTIVE.value == "Corrective"
    assert CapaActionType.PREVENTIVE.value == "Preventive"


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def _build_reviewed() -> QpReviewedPackage:
    """Minimal QpReviewedPackage for testing the report builder."""
    from app.domain.case_models import CaseComplianceResult
    from app.domain.compliance_models import ComplianceStatus
    from app.domain.investigation_models import (
        CitedClaim,
        InvestigationCaseResult,
        InvestigationDraft,
        InvestigationGateResult,
        RootCauseHypothesis,
    )
    from app.domain.master_data import RootCauseCategory
    from app.domain.models import ClassifiedCase, DeviationSeverity, DocType
    from app.domain.qp_models import (
        QpConfirmationAction,
        QpConfirmationOutcome,
        QpConfirmationRecord,
    )
    from app.domain.release_models import (
        CaseReleasePackage,
        ReleaseGateResult,
        ReleaseGateStatus,
    )

    classified = ClassifiedCase(
        tenant_id="ACME01",
        case_id="CASE-001",
        batch_id="BATCH-2026-001",
        product_name="AXR450 Injectable",
        doc_type=DocType.BATCH_RECORD,
        severity=DeviationSeverity.MAJOR,
        sterility_hold_seconds=86400,
        rationale="Out-of-specification assay result",
        classified_at=datetime.datetime(2026, 8, 29, 12, 0, 0, tzinfo=datetime.UTC),
    )
    case = CaseComplianceResult(
        classified=classified,
        aggregated_status=ComplianceStatus.INSUFFICIENT_EVIDENCE,
    )
    hypothesis = RootCauseHypothesis(
        category=RootCauseCategory.MATERIAL,
        statement="Raw material lot variability caused the OOS result.",
        confidence=0.72,
        supporting_claims=[
            CitedClaim(
                claim="CoA shows borderline assay value",
                source_uris=["https://erp.example.test/app/coa/CSR-0001"],
            )
        ],
    )
    draft = InvestigationDraft(
        tenant_id="ACME01",
        case_id="CASE-001",
        batch_id="BATCH-2026-001",
        hypotheses=[hypothesis],
        open_questions=["Was the same lot used in other batches?"],
    )
    investigation = InvestigationCaseResult(
        case=case,
        draft=draft,
        gate=InvestigationGateResult(
            passed=True,
            blocked_reasons=[],
        ),
    )
    release_gate = ReleaseGateResult(
        spec_id="annex16-v1",
        spec_version="1.0",
        spec_sha256="a" * 64,
        status=ReleaseGateStatus.READY_FOR_QP_REVIEW,
        items=[],
    )
    release_package = CaseReleasePackage(
        investigation=investigation,
        release_gate=release_gate,
    )
    confirmation = QpConfirmationRecord(
        action=QpConfirmationAction.CONFIRM,
        outcome=QpConfirmationOutcome.CONFIRMED,
        qp_name="Jane QP",
        rationale="Investigation is complete and the batch meets release criteria.",
        tenant_id="ACME01",
        case_id="CASE-001",
        batch_id="BATCH-2026-001",
        gate_status=ReleaseGateStatus.READY_FOR_QP_REVIEW,
        recorded_at=datetime.datetime(2026, 8, 30, 10, 0, 0, tzinfo=datetime.UTC),
    )
    return QpReviewedPackage(
        release_package=release_package,
        qp_confirmation=confirmation,
    )


def test_nonconformance_details_contains_case_identity() -> None:
    reviewed = _build_reviewed()
    details = build_nonconformance_details(reviewed)
    assert "CASE-001" in details
    assert "ACME01" in details
    assert "BATCH-2026-001" in details
    assert "decision_authority: none" in details


def test_nonconformance_subject_is_short() -> None:
    reviewed = _build_reviewed()
    subject = build_nonconformance_subject(reviewed)
    assert "CASE-001" in subject
    assert "confirmed" in subject.lower()
    assert len(subject) <= 256


def test_quality_action_problem_contains_case() -> None:
    reviewed = _build_reviewed()
    problem = build_quality_action_problem(reviewed)
    assert "CASE-001" in problem
    assert "QP decision" in problem


def test_quality_action_resolution_corrective() -> None:
    reviewed = _build_reviewed()
    resolution = build_quality_action_resolution(
        reviewed, action_type="Corrective"
    )
    assert "Corrective action" in resolution
    assert "Raw material lot variability" in resolution
    assert "decision_authority: none" in resolution


def test_quality_action_resolution_preventive() -> None:
    reviewed = _build_reviewed()
    resolution = build_quality_action_resolution(
        reviewed, action_type="Preventive"
    )
    assert "Preventive action" in resolution
    assert "other batches" in resolution
    assert "decision_authority: none" in resolution


# ---------------------------------------------------------------------------
# Source write methods
# ---------------------------------------------------------------------------


def _nc_response(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "countersign.erpnext.supply.v1",
        "tenant_id": "ACME01",
        "case_id": "CASE-001",
        "non_conformance_id": "NC-2026-0001",
        "non_conformance_url": (
            "https://erp.example.test/app/non-conformance/NC-2026-0001"
        ),
        "was_existing": False,
    }
    payload.update(overrides)
    return {"message": payload}


def _qa_response(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "countersign.erpnext.supply.v1",
        "tenant_id": "ACME01",
        "case_id": "CASE-001",
        "quality_action_id": "QA-2026-0001",
        "quality_action_url": (
            "https://erp.example.test/app/quality-action/QA-2026-0001"
        ),
        "was_existing": False,
    }
    payload.update(overrides)
    return {"message": payload}


def test_create_non_conformance_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _credentials(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def request_json_post(
        *, tenant_id: str, method: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append((tenant_id, method, body))
        return _nc_response()

    source = ErpNextSupplySource(request_json=lambda **_: {})  # not used for writes
    # Inject the post function directly
    import app.domain.department_source_erpnext as mod

    monkeypatch.setattr(mod, "_request_json_post", request_json_post)

    request = NonConformanceWriteRequest(
        tenant_id="ACME01",
        case_id="CASE-001",
        batch_id="BATCH-2026-001",
        subject="Test NC subject",
        details="Test NC details",
    )
    result = source.create_non_conformance(request=request)
    assert result.non_conformance_id == "NC-2026-0001"
    assert result.was_existing is False
    assert calls == [("ACME01", "create_non_conformance", request.model_dump(mode="json"))]


def test_create_non_conformance_rejects_scope_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    import app.domain.department_source_erpnext as mod

    monkeypatch.setattr(
        mod, "_request_json_post",
        lambda **_: _nc_response(tenant_id="OTHER"),
    )
    source = ErpNextSupplySource()
    with pytest.raises(ERPNextApiError, match="scope"):
        source.create_non_conformance(
            request=NonConformanceWriteRequest(
                tenant_id="ACME01",
                case_id="CASE-001",
                batch_id="BATCH-2026-001",
                subject="d",
                details="s",
            )
        )


def test_create_non_conformance_rejects_wrong_uri_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    import app.domain.department_source_erpnext as mod

    monkeypatch.setattr(
        mod, "_request_json_post",
        lambda **_: _nc_response(
            non_conformance_url="https://evil.test/app/non-conformance/NC-2026-0001"
        ),
    )
    source = ErpNextSupplySource()
    with pytest.raises(ERPNextApiError, match="host"):
        source.create_non_conformance(
            request=NonConformanceWriteRequest(
                tenant_id="ACME01",
                case_id="CASE-001",
                batch_id="BATCH-2026-001",
                subject="d",
                details="s",
            )
        )


def test_create_quality_action_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _credentials(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def request_json_post(
        *, tenant_id: str, method: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append((tenant_id, method, body))
        return _qa_response()

    import app.domain.department_source_erpnext as mod

    monkeypatch.setattr(mod, "_request_json_post", request_json_post)
    source = ErpNextSupplySource()

    request = QualityActionWriteRequest(
        tenant_id="ACME01",
        case_id="CASE-001",
        batch_id="BATCH-2026-001",
        non_conformance_id="NC-2026-0001",
        action_type=CapaActionType.CORRECTIVE,
        problem="Test problem",
        resolution="Test resolution",
    )
    result = source.create_quality_action(request=request)
    assert result.quality_action_id == "QA-2026-0001"
    assert result.was_existing is False
    assert calls == [("ACME01", "create_quality_action", request.model_dump(mode="json"))]


def test_create_quality_action_rejects_wrong_uri_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    import app.domain.department_source_erpnext as mod

    monkeypatch.setattr(
        mod, "_request_json_post",
        lambda **_: _qa_response(
            quality_action_url="https://erp.example.test/app/wrong-prefix/QA-2026-0001"
        ),
    )
    source = ErpNextSupplySource()
    with pytest.raises(ERPNextApiError, match="path"):
        source.create_quality_action(
            request=QualityActionWriteRequest(
                tenant_id="ACME01",
                case_id="CASE-001",
                batch_id="BATCH-2026-001",
                non_conformance_id="NC-2026-0001",
                action_type=CapaActionType.CORRECTIVE,
                problem="p",
                resolution="r",
            )
        )


# ---------------------------------------------------------------------------
# MCP tool functions
# ---------------------------------------------------------------------------


def test_capa_write_tools_allow_list_is_disjoint_from_qms() -> None:
    from app.domain.department_tools import QMS_WRITE_TOOLS

    assert CAPA_WRITE_TOOLS == {
        ERPNEXT_NONCONFORMANCE_WRITE,
        ERPNEXT_QUALITY_ACTION_WRITE,
    }
    assert CAPA_WRITE_TOOLS.isdisjoint(QMS_WRITE_TOOLS)


def test_erpnext_nonconformance_write_tenant_check() -> None:
    with pytest.raises(CapaTenantMismatch):
        erpnext_nonconformance_write(
            tenant_id="ACME01",
            case_id="CASE-001",
            batch_id="BATCH-2026-001",
            subject="d",
            details="s",
            case_tenant_id="OTHER",
        )


def test_erpnext_quality_action_write_tenant_check() -> None:
    with pytest.raises(CapaTenantMismatch):
        erpnext_quality_action_write(
            tenant_id="ACME01",
            case_id="CASE-001",
            batch_id="BATCH-2026-001",
            non_conformance_id="NC-001",
            action_type="Corrective",
            problem="p",
            resolution="r",
            case_tenant_id="OTHER",
        )


def test_erpnext_nonconformance_write_fails_closed_on_non_erpnext_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the CoA backend is not ERPNext, the tool must fail closed rather
    than silently writing to a system that is not the live supply source.
    """
    from app.domain.department_source import get_coa_source

    # Default backend is the synthetic catalog, not ERPNext
    assert get_coa_source().backend_name != BACKEND_ERPNEXT
    with pytest.raises(ErpNextCapaWriteError, match="erpnext"):
        erpnext_nonconformance_write(
            tenant_id="ACME01",
            case_id="CASE-001",
            batch_id="BATCH-2026-001",
            subject="d",
            details="s",
            case_tenant_id="ACME01",
        )


def test_erpnext_quality_action_write_rejects_bad_action_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsupported action_type must fail closed."""
    # Set up ERPNext backend so we get past the backend check
    monkeypatch.setenv("COUNTERSIGN_COA_BACKEND", "erpnext")
    reset_department_source_cache()
    _credentials(monkeypatch)
    with pytest.raises(ErpNextCapaWriteError, match="action_type"):
        erpnext_quality_action_write(
            tenant_id="ACME01",
            case_id="CASE-001",
            batch_id="BATCH-2026-001",
            non_conformance_id="NC-001",
            action_type="Invalid",
            problem="p",
            resolution="r",
            case_tenant_id="ACME01",
        )
