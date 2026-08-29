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

"""Unit tests for deterministic evidence validation and confidence routing.

Each test perturbs exactly one thing on a known-clean synthetic page, so a
failure names one cause. Together they are the ground-truth harness the
extraction stage is regression-tested against.
"""

import pytest

from app.domain.evidence_models import (
    BoundingBox,
    EvidenceStatus,
    FieldStatus,
    SourceAnchor,
)
from app.domain.evidence_validation import validate_extracted_evidence
from app.domain.master_data import load_document_field_spec
from tests.unit.evidence_fixtures import (
    GROUND_TRUTH_READINGS,
    extracted_field_from_reading,
    make_extracted_evidence,
    make_ingested_artifact,
    replace_field,
)


def _validate(evidence, page_count: int = 1):
    return validate_extracted_evidence(evidence, page_count=page_count)


def _field(result, field_name: str):
    for field in result.fields:
        if field.field_name == field_name:
            return field
    raise AssertionError(f"{field_name} not present in validation result")


def _perturbed(field_name: str, **reading_overrides):
    """Rebuilds evidence with one reading changed."""
    artifact = make_ingested_artifact()
    original = next(
        reading
        for reading in GROUND_TRUTH_READINGS
        if reading.field_name == field_name
    )
    changed = original.model_copy(update=reading_overrides)
    return replace_field(
        make_extracted_evidence(artifact=artifact),
        extracted_field_from_reading(artifact, changed),
    )


def test_clean_page_is_accepted_without_human_review() -> None:
    result = _validate(make_extracted_evidence())

    assert result.status is EvidenceStatus.ACCEPTED
    assert result.review_queue == []
    assert result.record_findings == []
    assert all(f.status is FieldStatus.ACCEPTED for f in result.fields)


def test_extracted_batch_must_match_case_scope() -> None:
    result = _validate(_perturbed("batch_number", value_text="AX-2292"))

    assert result.status is EvidenceStatus.HUMAN_REVIEW_REQUIRED
    assert any("batch_scope_mismatch" in finding for finding in result.record_findings)


def test_every_accepted_field_carries_a_source_anchor() -> None:
    """A fact with no pointer back to the record is not usable evidence."""
    result = _validate(make_extracted_evidence())

    for field in result.fields:
        assert field.anchor is not None
        assert field.anchor.artifact_sha256
        assert field.anchor.source_uri
        assert field.anchor.page_number == 1


def test_values_are_typed_deterministically_not_by_the_model() -> None:
    result = _validate(make_extracted_evidence())

    assert _field(result, "blend_speed_rpm").typed_value == 11.5
    assert _field(result, "page_number").typed_value == 14
    assert _field(result, "deviation_noted").typed_value is False
    assert _field(result, "step_start_time").typed_value == "2026-03-04T02:10:00"


def test_low_confidence_on_a_critical_field_routes_to_a_human() -> None:
    """The honest limitation, implemented as a control rather than hidden."""
    spec = load_document_field_spec()
    evidence = _perturbed("blend_speed_rpm", confidence=0.88)

    result = _validate(evidence)
    field = _field(result, "blend_speed_rpm")

    assert 0.88 > spec.standard_field_min_confidence  # would pass a lax bar
    assert field.status is FieldStatus.REVIEW_REQUIRED
    assert field.typed_value == 11.5  # the reading is preserved, not discarded
    assert "below_confidence_threshold" in field.findings[0]
    assert result.status is EvidenceStatus.HUMAN_REVIEW_REQUIRED
    assert "blend_speed_rpm" in result.review_queue


def test_same_confidence_on_a_standard_field_is_accepted() -> None:
    """Confirms the two-tier bar actually differs, not just that it exists."""
    result = _validate(_perturbed("product_temperature_c", confidence=0.88))

    assert _field(result, "product_temperature_c").status is FieldStatus.ACCEPTED
    assert result.status is EvidenceStatus.ACCEPTED


def test_out_of_specification_value_is_flagged_and_preserved() -> None:
    """An out-of-range reading is usually the deviation, so it must survive."""
    result = _validate(_perturbed("blend_speed_rpm", value_text="18.0"))
    field = _field(result, "blend_speed_rpm")

    assert field.status is FieldStatus.INVALID
    assert field.typed_value == 18.0
    assert field.value_text == "18.0"
    assert any("above_specification" in finding for finding in field.findings)
    assert result.status is EvidenceStatus.HUMAN_REVIEW_REQUIRED


def test_unit_mismatch_is_never_silently_converted() -> None:
    result = _validate(_perturbed("blend_speed_rpm", unit_text="Hz"))
    field = _field(result, "blend_speed_rpm")

    assert field.status is FieldStatus.INVALID
    assert any("unit_mismatch" in finding for finding in field.findings)


def test_ambiguous_number_is_a_parse_failure_not_a_guess() -> None:
    result = _validate(_perturbed("blend_speed_rpm", value_text="1,5"))
    field = _field(result, "blend_speed_rpm")

    assert field.status is FieldStatus.INVALID
    assert field.typed_value is None
    assert any("parse_failed" in finding for finding in field.findings)


def test_pattern_violation_is_flagged() -> None:
    result = _validate(_perturbed("equipment_id", value_text="EQ114"))
    field = _field(result, "equipment_id")

    assert field.status is FieldStatus.INVALID
    assert any("pattern_mismatch" in finding for finding in field.findings)


def test_value_outside_allowed_set_is_flagged() -> None:
    result = _validate(_perturbed("manufacturing_step", value_text="milling"))
    field = _field(result, "manufacturing_step")

    assert field.status is FieldStatus.INVALID
    assert any("value_not_permitted" in finding for finding in field.findings)


def test_required_field_absent_is_reported_as_missing() -> None:
    evidence = make_extracted_evidence()
    evidence = evidence.model_copy(
        update={
            "fields": [
                f for f in evidence.fields if f.field_name != "verifier_initials"
            ]
        }
    )

    result = _validate(evidence)
    field = _field(result, "verifier_initials")

    assert field.status is FieldStatus.MISSING
    assert field.typed_value is None
    assert any("required_field_absent" in finding for finding in field.findings)
    assert result.status is EvidenceStatus.HUMAN_REVIEW_REQUIRED


def test_declared_unreadable_field_is_reported_without_a_guess() -> None:
    evidence = make_extracted_evidence()
    evidence = evidence.model_copy(
        update={
            "fields": [
                f for f in evidence.fields if f.field_name != "operator_initials"
            ],
            "unreadable_field_names": ["operator_initials"],
        }
    )

    result = _validate(evidence)
    field = _field(result, "operator_initials")

    assert field.status is FieldStatus.MISSING
    assert any("reported_unreadable" in finding for finding in field.findings)


def test_anchor_citing_a_page_the_artifact_lacks_is_flagged() -> None:
    """The page bound comes from the artifact, not from the extractor."""
    result = _validate(_perturbed("batch_number", page_number=7))
    field = _field(result, "batch_number")

    assert field.status is FieldStatus.INVALID
    assert any("anchor_page_out_of_range" in finding for finding in field.findings)


def test_degenerate_bounding_box_is_flagged() -> None:
    artifact = make_ingested_artifact()
    reading = GROUND_TRUTH_READINGS[0].model_copy(
        update={
            "bounding_box": BoundingBox(
                x_min=0.4, y_min=0.4, x_max=0.4, y_max=0.4
            )
        }
    )
    evidence = replace_field(
        make_extracted_evidence(artifact=artifact),
        extracted_field_from_reading(artifact, reading),
    )

    field = _field(_validate(evidence), reading.field_name)

    assert field.status is FieldStatus.INVALID
    assert any("bounding_box_degenerate" in finding for finding in field.findings)


def test_unlocatable_reading_is_flagged() -> None:
    artifact = make_ingested_artifact()
    reading = GROUND_TRUTH_READINGS[0].model_copy(
        update={"bounding_box": None, "transcribed_context": ""}
    )
    evidence = replace_field(
        make_extracted_evidence(artifact=artifact),
        extracted_field_from_reading(artifact, reading),
    )

    field = _field(_validate(evidence), reading.field_name)

    assert field.status is FieldStatus.INVALID
    assert any("anchor_unlocatable" in finding for finding in field.findings)


def test_chronology_inversion_is_a_record_level_finding() -> None:
    result = _validate(_perturbed("step_end_time", value_text="2026-03-04 02:05"))

    assert any(
        "chronology_invalid" in finding for finding in result.record_findings
    )
    assert result.status is EvidenceStatus.HUMAN_REVIEW_REQUIRED


def test_recorded_duration_must_agree_with_recorded_times() -> None:
    """Two records of one fact that disagree is a data-integrity finding."""
    result = _validate(_perturbed("blend_time_min", value_text="12"))

    assert any(
        "duration_inconsistent" in finding for finding in result.record_findings
    )


def test_missing_second_person_verification_is_flagged() -> None:
    result = _validate(_perturbed("verifier_initials", value_text="JMR"))

    assert any(
        "second_person_verification_absent" in finding
        for finding in result.record_findings
    )
    assert result.status is EvidenceStatus.HUMAN_REVIEW_REQUIRED


def test_field_outside_the_approved_spec_is_reported_not_merged() -> None:
    evidence = make_extracted_evidence(
        unspecified_field_names=["operator_mood"]
    )

    result = _validate(evidence)

    assert "operator_mood" not in {f.field_name for f in result.fields}
    assert any(
        "unspecified_field_discarded" in finding
        for finding in result.record_findings
    )


def test_validation_refuses_a_different_specification() -> None:
    evidence = make_extracted_evidence().model_copy(
        update={"spec_sha256": "0" * 64, "spec_version": "0.9.0"}
    )

    with pytest.raises(ValueError, match="extracted under spec"):
        _validate(evidence)


def test_audit_event_records_the_full_control_chain() -> None:
    evidence = _perturbed("blend_speed_rpm", confidence=0.10)

    audit = _validate(evidence).audit

    assert audit.event_type == "evidence_extraction_validated"
    assert audit.decision_authority == "none"
    assert audit.artifact_sha256 == evidence.artifact_sha256
    assert audit.spec_sha256 == evidence.spec_sha256
    assert audit.extractor_model == evidence.extractor_model
    assert audit.status is EvidenceStatus.HUMAN_REVIEW_REQUIRED
    assert audit.review_queue == ["blend_speed_rpm"]
    assert audit.review_field_count == 1
    assert audit.accepted_field_count == len(GROUND_TRUTH_READINGS) - 1


def test_no_record_claims_decision_authority() -> None:
    result = _validate(make_extracted_evidence())

    assert result.decision_authority == "none"
    assert result.audit.decision_authority == "none"


def test_anchor_provenance_cannot_be_supplied_by_the_model() -> None:
    """SourceAnchor requires artifact identity the extractor never sees."""
    with pytest.raises(ValueError):
        SourceAnchor(**{"source_uri": "drive://x", "page_number": 1})  # type: ignore[call-arg]
