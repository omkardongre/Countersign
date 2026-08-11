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

"""Deterministic GMP deviation severity classification.

Pure functions, no model call, no I/O. Per the Annex 22 draft posture
(``final_reseach/05_architecture.md`` section 0 and
``final_reseach/06_review_and_corrections.md`` section A1): "The LLM is never
in the critical decision path." Severity is exactly the kind of critical GMP
determination that must stay in deterministic code, and it also becomes the
Gateway's ``lifecycle_state`` dimension once the case is created, so the rule
that fired must be recorded for audit (``rationale``).

Rule ordering is deliberately most-severe-first: any single trigger promotes
the case, so evaluation short-circuits on the first match at each tier.
"""

from __future__ import annotations

from app.domain.models import CaseIntake, DeviationSeverity

# Recurrence threshold: three or more similar deviations for the same
# product/line inside 90 days indicates a systemic issue, not an isolated
# event, and is escalated one tier per standard CDMO deviation-management
# practice (recurring deviations require root-cause escalation, not repeated
# isolated investigations).
RECURRENCE_ESCALATION_THRESHOLD = 3


def classify_severity(intake: CaseIntake) -> tuple[DeviationSeverity, str]:
    """Classifies a deviation's severity from structured intake signals.

    Returns:
        A ``(severity, rationale)`` pair. ``rationale`` names the exact rule
        that fired, so the classification is auditable independent of any
        model — a QP or auditor can verify the determination without
        re-running anything.
    """
    if intake.patient_safety_impact:
        return (
            DeviationSeverity.CRITICAL,
            "patient_safety_impact=true",
        )
    if intake.sterility_assurance_impact:
        return (
            DeviationSeverity.CRITICAL,
            "sterility_assurance_impact=true",
        )
    if intake.product_quality_impact == "confirmed":
        return (
            DeviationSeverity.CRITICAL,
            "product_quality_impact=confirmed",
        )
    if intake.regulatory_reportable:
        return (
            DeviationSeverity.CRITICAL,
            "regulatory_reportable=true",
        )

    if intake.affects_release_criteria:
        return (
            DeviationSeverity.MAJOR,
            "affects_release_criteria=true",
        )
    if intake.product_quality_impact == "potential":
        return (
            DeviationSeverity.MAJOR,
            "product_quality_impact=potential",
        )
    if intake.prior_occurrences_90d >= RECURRENCE_ESCALATION_THRESHOLD:
        return (
            DeviationSeverity.MAJOR,
            f"prior_occurrences_90d>={RECURRENCE_ESCALATION_THRESHOLD} (recurring, systemic)",
        )

    return (
        DeviationSeverity.MINOR,
        "no critical or major trigger matched",
    )
