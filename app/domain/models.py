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

"""Case data model for the Countersign deviation-investigation workflow.

Field/state vocabulary follows Veeva Vault QMS's Agent Card model
(``final_reseach/02_veeva_vault_qms.md`` section 2-3): every case carries a
``tenant_scope`` and a ``lifecycle_state`` because the Gateway's authorization
tuple is ``(agent, tool, data_class, lifecycle_state, tenant)``
(``final_reseach/05_architecture.md`` section 4.1/4.3). ``decision_authority``
is fixed to ``"none"`` on every record — no agent produced by this workflow is
ever the record of decision; per the Annex 22 draft posture
(``final_reseach/05_architecture.md`` section 0), the LLM is never in the
critical decision path.
"""

from __future__ import annotations

import datetime
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field


class DeviationSeverity(StrEnum):
    """GMP deviation classification tier.

    Three-tier minor/major/critical classification is the standard CDMO
    deviation-management scheme referenced in
    ``final_reseach/06_review_and_corrections.md`` section B ("Real sequence
    is detect -> record -> contain -> classify (minor/major/critical) ->
    investigate proportionally"). Severity drives investigation depth and,
    downstream, the Gateway's lifecycle-state authorization dimension.
    """

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class LifecycleState(StrEnum):
    """Case lifecycle state — the Gateway's Atomic-Security dimension.

    Mirrors Veeva Atomic Security: the same agent has different tool
    permissions depending on where the record sits in this sequence
    (``final_reseach/02_veeva_vault_qms.md`` section 3, layer 4).
    """

    DETECTED = "detected"
    CONTAINED = "contained"
    CLASSIFIED = "classified"
    UNDER_INVESTIGATION = "under_investigation"
    PENDING_QP = "pending_qp"
    DISPOSITIONED = "dispositioned"


class DocType(StrEnum):
    """Source document type driving intake routing."""

    BATCH_RECORD = "batch_record"
    LAB_RESULT = "lab_result"
    SUPPLIER_COA = "supplier_coa"
    OPERATOR_REPORT = "operator_report"
    ENVIRONMENTAL_LOG = "environmental_log"


class CaseIntake(BaseModel):
    """Raw intake payload for a newly detected deviation.

    This is the workflow's ``START`` input — structured fields captured by
    manufacturing/QA at the point of detection, not free text. Classification
    is deterministic code over these fields (Annex 22 draft excludes LLMs
    from critical GMP applications; see ``final_reseach/05_architecture.md``
    section 0), so every field the classifier reads must be here, typed.
    """

    tenant_id: str = Field(
        description="Sponsor client scope. Never mixed across cases (Annex 16 s1.4.3)."
    )
    batch_id: str
    product_name: str
    doc_type: DocType
    description: str = Field(
        description="Human-entered summary; not parsed for classification."
    )
    reported_by: str
    detected_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )

    # --- Structured classification signals (set by the reporter, not inferred) ---
    patient_safety_impact: bool = Field(
        default=False,
        description="Deviation could plausibly affect patient safety.",
    )
    sterility_assurance_impact: bool = Field(
        default=False,
        description="Deviation affects sterility assurance (e.g. autoclave, aseptic fill).",
    )
    sterility_hold_seconds: int = Field(
        gt=0,
        description=(
            "QA-specified sterility incubation hold, in seconds, for this "
            "batch's product. Required: product-specific pharmacopeia "
            "sterility test durations are not all the same, so there is no "
            "platform-wide default to fall back to. QA sets this at intake, "
            "entered on the intake UI as separate days/hours/minutes fields "
            "and combined into one duration before it reaches this model — "
            "a real case is set in whole days, a local/dev test case can be "
            "set to just a few minutes without touching any global override."
        ),
    )
    product_quality_impact: str = Field(
        default="none",
        description='One of "none", "potential", "confirmed".',
    )
    affects_release_criteria: bool = Field(
        default=False,
        description="Deviation touches a specification the batch must meet to release.",
    )
    regulatory_reportable: bool = Field(
        default=False,
        description="Deviation meets a pre-defined regulatory reporting trigger.",
    )
    prior_occurrences_90d: int = Field(
        default=0,
        ge=0,
        description="Count of similar deviations for this product/line in the last 90 days.",
    )


class ClassifiedCase(BaseModel):
    """Output of deterministic intake + classification.

    ``decision_authority`` is always ``"none"`` — see module docstring.
    """

    case_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    batch_id: str
    product_name: str
    doc_type: DocType
    severity: DeviationSeverity
    lifecycle_state: LifecycleState = LifecycleState.CLASSIFIED
    decision_authority: str = "none"
    sterility_hold_seconds: int = Field(
        gt=0,
        description="Carried from intake. QA sets the hold; there is no platform default.",
    )
    patient_safety_impact: bool = Field(
        default=False,
        description=(
            "Carried from structured intake for deterministic downstream routing; "
            "never inferred from the deviation description."
        ),
    )
    prior_occurrences_90d: int = Field(
        default=0,
        ge=0,
        description=(
            "Carried from structured intake for deterministic downstream routing; "
            "not a model-generated recurrence count."
        ),
    )
    rationale: str = Field(
        description="Which classification rule fired, for audit traceability."
    )
    classified_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
