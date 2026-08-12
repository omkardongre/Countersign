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

"""Unit tests for the deterministic GMP compliance engine.

Each test starts from a clean, fully-validated synthetic page and perturbs one
``ValidatedField``, so a failing assertion names exactly one cause — same
discipline as ``test_evidence_validation.py``.
"""

from __future__ import annotations

import pytest

from app.domain.compliance import (
    check_completeness,
    check_specification_conformance,
    check_trend_conformance,
    evaluate_compliance,
    resolve_compliance_result,
)
from app.domain.compliance_models import (
    ComplianceFindingType,
    ComplianceStatus,
    OosPhase,
)
from app.domain.evidence_models import FieldStatus
from app.domain.master_data import (
    RootCauseCategory,
    load_document_field_spec,
    load_trend_baseline,
)
from tests.unit.evidence_fixtures import make_validated_evidence


def _replace_validated_field(evidence, field_name: str, **overrides):
    """Returns a copy of ``evidence`` with one ``ValidatedField`` perturbed."""
    fields = []
    for field in evidence.fields:
        if field.field_name == field_name:
            fields.append(field.model_copy(update=overrides))
        else:
            fields.append(field)
    return evidence.model_copy(update={"fields": fields})


def _drop_field(evidence, field_name: str):
    fields = [f for f in evidence.fields if f.field_name != field_name]
    return evidence.model_copy(update={"fields": fields})


def _finding(result, field_name: str, finding_type: ComplianceFindingType | None = None):
    for finding in result.findings:
        if finding.field_name == field_name and (
            finding_type is None or finding.finding_type is finding_type
        ):
            return finding
    return None


@pytest.fixture
def baseline():
    return load_trend_baseline("trend_baseline_axr450_v1.json")


def test_clean_evidence_is_sufficient_for_investigation(baseline) -> None:
    evidence = make_validated_evidence()

    result = evaluate_compliance(evidence, baseline=baseline)

    assert result.status is ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION
    assert result.findings == []
    assert result.decision_authority == "none"
    assert result.audit.decision_authority == "none"


def test_result_carries_source_evidence_forward_for_independent_audit() -> None:
    evidence = make_validated_evidence()

    result = evaluate_compliance(evidence)

    assert result.source_evidence == evidence


# --- Completeness -----------------------------------------------------------


def test_missing_required_field_is_a_completeness_finding() -> None:
    evidence = _drop_field(make_validated_evidence(), "verifier_initials")

    result = evaluate_compliance(evidence)
    finding = _finding(result, "verifier_initials", ComplianceFindingType.COMPLETENESS)

    assert finding is not None
    assert finding.root_cause_category is RootCauseCategory.PERSONNEL
    assert result.status is ComplianceStatus.INSUFFICIENT_EVIDENCE


def test_unverified_field_counts_as_incomplete_not_settled() -> None:
    """A low-confidence reading pending human review is not yet part of the
    record, even though the field is technically present."""
    evidence = _replace_validated_field(
        make_validated_evidence(),
        "blend_speed_rpm",
        status=FieldStatus.REVIEW_REQUIRED,
    )

    result = evaluate_compliance(evidence)
    finding = _finding(result, "blend_speed_rpm", ComplianceFindingType.COMPLETENESS)

    assert finding is not None
    assert "not cleared human confidence review" in finding.description
    assert result.status is ComplianceStatus.INSUFFICIENT_EVIDENCE


def test_completeness_check_is_isolable_from_the_others() -> None:
    """check_completeness alone must not raise or require a baseline."""
    evidence = _drop_field(make_validated_evidence(), "operator_initials")

    findings = check_completeness(load_document_field_spec(), evidence)

    assert len(findings) == 1
    assert findings[0].field_name == "operator_initials"


# --- Specification conformance (OOS) ----------------------------------------


def test_out_of_range_value_is_an_oos_finding_at_phase_i() -> None:
    """No assignable cause on record yet -- Phase I, per the Barr Labs rule."""
    evidence = _replace_validated_field(
        make_validated_evidence(),
        "blend_speed_rpm",
        status=FieldStatus.INVALID,
        typed_value=18.0,
        value_text="18.0",
        findings=["above_specification: 18.0 is over the maximum 14.0 rpm"],
    )

    result = evaluate_compliance(evidence)
    finding = _finding(result, "blend_speed_rpm", ComplianceFindingType.OUT_OF_SPECIFICATION)

    assert finding is not None
    assert finding.oos_phase is OosPhase.PHASE_I_LABORATORY
    assert finding.root_cause_category is RootCauseCategory.MACHINE
    assert finding.observed_value == "18.0"
    assert finding.expected_value == "8.0-14.0 rpm"
    assert result.status is ComplianceStatus.INSUFFICIENT_EVIDENCE


def test_transcription_only_failure_is_oos_at_phase_ii() -> None:
    """A pattern/parse failure is an assignable transcription cause already on
    record, so the finding starts past Phase I scope."""
    evidence = _replace_validated_field(
        make_validated_evidence(),
        "equipment_id",
        status=FieldStatus.INVALID,
        typed_value=None,
        findings=["pattern_mismatch: 'EQ114' does not match the specified format"],
    )

    result = evaluate_compliance(evidence)
    finding = _finding(result, "equipment_id", ComplianceFindingType.OUT_OF_SPECIFICATION)

    assert finding is not None
    assert finding.oos_phase is OosPhase.PHASE_II_FULL_SCALE


@pytest.mark.parametrize(
    "field_findings",
    [
        ["unit_absent: spec requires the value to be reported in 'rpm'"],
        ["unexpected_unit: read unit 'Hz' on a field the spec declares unitless"],
        ["empty_transcription: extractor returned a blank value"],
        ["anchor_bounding_box_degenerate: box encloses no area"],
        ["anchor_unlocatable: no bounding box and no transcribed context"],
    ],
)
def test_non_range_findings_never_default_to_phase_i(field_findings) -> None:
    """Every finding shape emitted by evidence_validation other than a pure
    range violation must land at Phase II -- an assignable cause (bad unit,
    blank read, unlocatable anchor) is already on record for each of these,
    so treating them as "no assignable cause yet" would be wrong."""
    evidence = _replace_validated_field(
        make_validated_evidence(),
        "blend_speed_rpm",
        status=FieldStatus.INVALID,
        typed_value=None,
        findings=field_findings,
    )

    result = evaluate_compliance(evidence)
    finding = _finding(result, "blend_speed_rpm", ComplianceFindingType.OUT_OF_SPECIFICATION)

    assert finding is not None
    assert finding.oos_phase is OosPhase.PHASE_II_FULL_SCALE


def test_range_finding_mixed_with_a_locatability_finding_is_phase_ii() -> None:
    """A field that is BOTH out of range AND poorly anchored is not cleanly
    Phase I -- something about reading it is already suspect too."""
    evidence = _replace_validated_field(
        make_validated_evidence(),
        "blend_speed_rpm",
        status=FieldStatus.INVALID,
        typed_value=18.0,
        value_text="18.0",
        findings=[
            "above_specification: 18.0 is over the maximum 14.0 rpm",
            "anchor_unlocatable: no bounding box and no transcribed context",
        ],
    )

    result = evaluate_compliance(evidence)
    finding = _finding(result, "blend_speed_rpm", ComplianceFindingType.OUT_OF_SPECIFICATION)

    assert finding is not None
    assert finding.oos_phase is OosPhase.PHASE_II_FULL_SCALE


def test_review_required_field_is_not_yet_an_oos_finding() -> None:
    """Not settled yet -- this is a completeness gap, not an OOS trigger."""
    evidence = _replace_validated_field(
        make_validated_evidence(),
        "blend_speed_rpm",
        status=FieldStatus.REVIEW_REQUIRED,
    )

    result = evaluate_compliance(evidence)

    assert _finding(result, "blend_speed_rpm", ComplianceFindingType.OUT_OF_SPECIFICATION) is None
    assert _finding(result, "blend_speed_rpm", ComplianceFindingType.COMPLETENESS) is not None


def test_specification_conformance_check_does_not_require_a_baseline() -> None:
    evidence = _replace_validated_field(
        make_validated_evidence(),
        "product_temperature_c",
        status=FieldStatus.INVALID,
        typed_value=30.0,
        findings=["above_specification: 30.0 is over the maximum 26.0 degC"],
    )

    findings = check_specification_conformance(load_document_field_spec(), evidence)

    assert len(findings) == 1
    assert findings[0].field_name == "product_temperature_c"


# --- Trend conformance (OOT) -------------------------------------------------


def test_in_spec_value_far_from_trend_baseline_is_an_oot_finding(baseline) -> None:
    """The headline OOT property: in specification, still flagged."""
    # 8.0-14.0 rpm is the spec range; 13.9 is in-spec but far from the
    # baseline mean of 11.2 +/- 0.6 (4.5 sigma).
    evidence = _replace_validated_field(
        make_validated_evidence(),
        "blend_speed_rpm",
        typed_value=13.9,
    )

    result = evaluate_compliance(evidence, baseline=baseline)
    finding = _finding(result, "blend_speed_rpm", ComplianceFindingType.OUT_OF_TREND)

    assert finding is not None
    assert finding.sigma_distance is not None and finding.sigma_distance > 3.0
    assert _finding(result, "blend_speed_rpm", ComplianceFindingType.OUT_OF_SPECIFICATION) is None
    assert result.status is ComplianceStatus.INSUFFICIENT_EVIDENCE


def test_value_within_sigma_threshold_is_not_out_of_trend(baseline) -> None:
    evidence = make_validated_evidence()  # 11.5 rpm, close to the 11.2 mean

    result = evaluate_compliance(evidence, baseline=baseline)

    assert _finding(result, "blend_speed_rpm", ComplianceFindingType.OUT_OF_TREND) is None


def test_untracked_field_is_never_trend_checked(baseline) -> None:
    """product_code has no numeric trend and is not marked trend_tracked."""
    findings = check_trend_conformance(
        load_document_field_spec(), make_validated_evidence(), baseline
    )

    assert all(f.field_name != "product_code" for f in findings)


def test_missing_baseline_skips_oot_entirely_rather_than_passing_silently() -> None:
    evidence = _replace_validated_field(
        make_validated_evidence(), "blend_speed_rpm", typed_value=13.9
    )

    result = evaluate_compliance(evidence, baseline=None)

    assert result.trend_baseline_id is None
    assert all(f.finding_type is not ComplianceFindingType.OUT_OF_TREND for f in result.findings)
    # Confirm this is "not evaluated", not "evaluated and clean": the same
    # value with the baseline supplied does produce a finding.
    baseline = load_trend_baseline("trend_baseline_axr450_v1.json")
    with_baseline = evaluate_compliance(evidence, baseline=baseline)
    assert _finding(with_baseline, "blend_speed_rpm", ComplianceFindingType.OUT_OF_TREND) is not None


def test_oot_check_skips_a_baseline_with_an_unrecognised_method() -> None:
    baseline = load_trend_baseline("trend_baseline_axr450_v1.json").model_copy(
        update={"method": "ewma"}
    )
    evidence = _replace_validated_field(
        make_validated_evidence(), "blend_speed_rpm", typed_value=13.9
    )

    findings = check_trend_conformance(load_document_field_spec(), evidence, baseline)

    assert findings == []


# --- Root-cause summary ------------------------------------------------------


def test_root_cause_summary_counts_findings_per_dimension() -> None:
    evidence = make_validated_evidence()
    evidence = _drop_field(evidence, "verifier_initials")  # personnel
    evidence = _drop_field(evidence, "operator_initials")  # personnel

    result = evaluate_compliance(evidence)

    assert result.root_cause_summary[RootCauseCategory.PERSONNEL] == 2


def test_no_findings_means_empty_root_cause_summary() -> None:
    result = evaluate_compliance(make_validated_evidence())

    assert result.root_cause_summary == {}


# --- Spec-mismatch guard -----------------------------------------------------


def test_refuses_a_different_specification() -> None:
    evidence = make_validated_evidence().model_copy(
        update={"spec_sha256": "0" * 64, "spec_version": "0.9.0"}
    )

    with pytest.raises(ValueError, match="validated under spec"):
        evaluate_compliance(evidence)


def test_refuses_a_spec_id_mismatch_even_with_a_coincidental_hash_match() -> None:
    evidence = make_validated_evidence().model_copy(update={"spec_id": "other_doc_type"})

    with pytest.raises(ValueError, match="validated under spec id"):
        evaluate_compliance(evidence)


def test_refuses_a_baseline_for_the_wrong_product(baseline) -> None:
    """A caller-supplied baseline must be checked against the evidence's own
    product_code reading, not only trusted because it was passed in."""
    wrong_product_baseline = baseline.model_copy(update={"product_code": "ZZZ9999"})

    with pytest.raises(ValueError, match="approved for product"):
        evaluate_compliance(make_validated_evidence(), baseline=wrong_product_baseline)


def test_baseline_guard_is_silent_when_product_code_is_unsettled(baseline) -> None:
    """Cannot check a match that has no settled value to compare -- that gap
    is already surfaced as a completeness finding, not raised here too."""
    evidence = _replace_validated_field(
        make_validated_evidence(), "product_code", status=FieldStatus.REVIEW_REQUIRED
    )
    wrong_product_baseline = baseline.model_copy(update={"product_code": "ZZZ9999"})

    result = evaluate_compliance(evidence, baseline=wrong_product_baseline)

    assert _finding(result, "product_code", ComplianceFindingType.COMPLETENESS) is not None


# --- Node wiring --------------------------------------------------------------


def test_node_resolves_baseline_from_the_evidence_product_code() -> None:
    result = resolve_compliance_result(make_validated_evidence())

    assert result.trend_baseline_id == "axr450_blending_trend_baseline"
    assert result.status is ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION


def test_node_skips_baseline_when_product_code_is_unsettled() -> None:
    evidence = _replace_validated_field(
        make_validated_evidence(), "product_code", status=FieldStatus.REVIEW_REQUIRED
    )

    result = resolve_compliance_result(evidence)

    assert result.trend_baseline_id is None


def test_node_skips_baseline_for_a_product_with_no_approved_baseline() -> None:
    evidence = _replace_validated_field(
        make_validated_evidence(), "product_code", typed_value="ZZZ9999"
    )

    result = resolve_compliance_result(evidence)

    assert result.trend_baseline_id is None


def test_no_record_claims_decision_authority() -> None:
    result = resolve_compliance_result(make_validated_evidence())

    assert result.decision_authority == "none"
    assert result.audit.decision_authority == "none"
