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

"""Unit tests for deterministic GMP deviation severity classification."""

from app.domain.classification import (
    RECURRENCE_ESCALATION_THRESHOLD,
    classify_severity,
)
from app.domain.models import CaseIntake, DeviationSeverity, DocType


def _make_intake(**overrides) -> CaseIntake:
    defaults = {
        "tenant_id": "aurex",
        "batch_id": "AX-2291",
        "product_name": "Test Product",
        "doc_type": DocType.BATCH_RECORD,
        "description": "unit test intake",
        "reported_by": "test-operator",
        "sterility_hold_seconds": 14 * 24 * 60 * 60,
    }
    defaults.update(overrides)
    return CaseIntake(**defaults)


def test_default_intake_classifies_minor() -> None:
    intake = _make_intake()
    severity, rationale = classify_severity(intake)
    assert severity == DeviationSeverity.MINOR
    assert "no critical or major trigger" in rationale


def test_patient_safety_impact_is_critical() -> None:
    intake = _make_intake(patient_safety_impact=True)
    severity, rationale = classify_severity(intake)
    assert severity == DeviationSeverity.CRITICAL
    assert "patient_safety_impact" in rationale


def test_sterility_assurance_impact_is_critical() -> None:
    intake = _make_intake(sterility_assurance_impact=True)
    severity, _ = classify_severity(intake)
    assert severity == DeviationSeverity.CRITICAL


def test_confirmed_product_quality_impact_is_critical() -> None:
    intake = _make_intake(product_quality_impact="confirmed")
    severity, _ = classify_severity(intake)
    assert severity == DeviationSeverity.CRITICAL


def test_regulatory_reportable_is_critical() -> None:
    intake = _make_intake(regulatory_reportable=True)
    severity, _ = classify_severity(intake)
    assert severity == DeviationSeverity.CRITICAL


def test_affects_release_criteria_is_major() -> None:
    intake = _make_intake(affects_release_criteria=True)
    severity, rationale = classify_severity(intake)
    assert severity == DeviationSeverity.MAJOR
    assert "affects_release_criteria" in rationale


def test_potential_product_quality_impact_is_major() -> None:
    intake = _make_intake(product_quality_impact="potential")
    severity, _ = classify_severity(intake)
    assert severity == DeviationSeverity.MAJOR


def test_recurrence_below_threshold_stays_minor() -> None:
    intake = _make_intake(
        prior_occurrences_90d=RECURRENCE_ESCALATION_THRESHOLD - 1
    )
    severity, _ = classify_severity(intake)
    assert severity == DeviationSeverity.MINOR


def test_recurrence_at_threshold_escalates_to_major() -> None:
    intake = _make_intake(
        prior_occurrences_90d=RECURRENCE_ESCALATION_THRESHOLD
    )
    severity, rationale = classify_severity(intake)
    assert severity == DeviationSeverity.MAJOR
    assert "recurring" in rationale


def test_critical_signal_outranks_major_signal() -> None:
    """A critical trigger must win even if a major trigger is also present."""
    intake = _make_intake(
        patient_safety_impact=True,
        affects_release_criteria=True,
    )
    severity, rationale = classify_severity(intake)
    assert severity == DeviationSeverity.CRITICAL
    assert "patient_safety_impact" in rationale
