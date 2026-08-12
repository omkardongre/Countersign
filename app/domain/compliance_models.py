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

"""Schemas for the deterministic GMP compliance engine.

The compliance engine consumes ``ValidatedEvidence`` and asks three separate
questions, because the regulatory literature treats them as three separate
questions rather than one blended check:

1. **Completeness** — is every field the approved specification requires
   present, human-verified where flagged, and free of unresolved conflicts?
   ICH Q7 batch record review requires demonstrated completeness before any
   downstream disposition step, and batch record review guidance frames this
   as a precondition distinct from whether individual values conform.

2. **Specification conformance (OOS)** — does an in-range-looking value
   actually sit inside its approved acceptance range? FDA's OOS guidance
   (1993, revised 2022) is explicit that an out-of-specification result opens
   a formal two-phase investigation — Phase I laboratory investigation for
   assignable cause, Phase II full-scale investigation when no lab error is
   found — and that **retesting must not occur before that investigation is
   opened** (the Barr Laboratories precedent). This module records that an
   OOS trigger fired and which phase gate it is waiting on; it does not
   perform Phase I/II itself, because determining assignable cause is
   necessarily a human judgement, not a deterministic check.

3. **Trend conformance (OOT)** — is a value that sits inside its
   specification nonetheless inconsistent with how this process has actually
   been running? USP <1010> and the pharmaceutical OOT literature are explicit
   that OOT is evaluated against historical batch data, not the specification
   limit, and that a result can be simultaneously in-spec and out-of-trend.
   This is why ``TrendBaseline`` (``app.domain.master_data``) is separate
   master data from the field specification's acceptance range.

A fourth axis, root-cause categorisation, is not a check at all — it is a tag
carried on every finding so the (future) Investigation agent receives a
starting hypothesis dimension instead of a bare field name, per Katalyze's
published Deviation Investigator framing (material/machine/method/measure;
``final_reseach/01_katalyze_ai.md`` section 3).

``decision_authority`` stays ``"none"`` throughout. Nothing in this module
releases a batch, closes a deviation, or assigns a root cause — it only
determines whether the case has, and cites, enough conforming evidence to
proceed to a human-led investigation.
"""

from __future__ import annotations

import datetime
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.evidence_models import SourceAnchor, ValidatedEvidence
from app.domain.master_data import RootCauseCategory


class ComplianceFindingType(StrEnum):
    """Which of the three regulatory questions a finding answers."""

    COMPLETENESS = "completeness"
    OUT_OF_SPECIFICATION = "out_of_specification"
    OUT_OF_TREND = "out_of_trend"


class OosPhase(StrEnum):
    """Which phase of the FDA OOS investigation an OOS finding is waiting on.

    Set once, by the engine, from whether an assignable lab/transcription
    cause is already known from validation — never advanced automatically.
    Only a human investigation can conclude a phase; this field records where
    the finding stands, not a determination the engine has made.
    """

    PHASE_I_LABORATORY = "phase_i_laboratory"
    """No assignable cause yet on record. Retesting must not occur until a
    formal investigation is opened — the Barr Laboratories rule."""

    PHASE_II_FULL_SCALE = "phase_ii_full_scale"
    """An assignable transcription/legibility cause is already on record from
    evidence validation (e.g. the field was ``invalid`` for a parse reason,
    not just out of range), so the finding starts past Phase I scope."""


class ComplianceFinding(BaseModel):
    """One deterministic finding, always carrying its evidence pointer.

    Never discarded, never summarised away: a QP or auditor must be able to
    trace every finding back to the exact reading it came from, matching
    Katalyze's "every step carries the source it used" and MasterControl's
    exception-review pattern.
    """

    finding_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    finding_type: ComplianceFindingType
    field_name: str
    label: str
    root_cause_category: RootCauseCategory
    description: str = Field(
        description="Human-readable statement of exactly what failed and why."
    )
    anchor: SourceAnchor | None = Field(
        default=None,
        description=(
            "Absent only for a completeness finding on a field the extractor "
            "never produced a reading for at all — there is no page location "
            "to point to for a fact that was never transcribed."
        ),
    )
    observed_value: str | None = None
    expected_value: str | None = Field(
        default=None,
        description=(
            "Specification range or trend baseline the value was checked "
            "against, rendered for the finding — not a corrected value."
        ),
    )
    oos_phase: OosPhase | None = Field(
        default=None,
        description="Set only when finding_type is out_of_specification.",
    )
    sigma_distance: float | None = Field(
        default=None,
        description="Set only when finding_type is out_of_trend.",
    )


class ComplianceStatus(StrEnum):
    """Whether the case's evidence is sufficient to proceed to investigation.

    Not a disposition. ``SUFFICIENT_FOR_INVESTIGATION`` means the evidence is
    complete and conformance-checked enough to hand to a human-led
    investigation — it does not mean the batch may release, and it says
    nothing about root cause.
    """

    SUFFICIENT_FOR_INVESTIGATION = "sufficient_for_investigation"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ComplianceAuditEvent(BaseModel):
    """Immutable record that the compliance engine ran, and what it found."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = "compliance_check_evaluated"
    tenant_id: str
    case_id: str
    batch_id: str
    artifact_filename: str
    artifact_version: int
    artifact_sha256: str
    spec_id: str
    spec_version: str
    spec_sha256: str
    trend_baseline_id: str | None = None
    trend_baseline_version: str | None = None
    trend_baseline_sha256: str | None = None
    status: ComplianceStatus
    completeness_finding_count: int
    oos_finding_count: int
    oot_finding_count: int
    recorded_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    decision_authority: str = "none"


class ComplianceResult(BaseModel):
    """Output of the deterministic compliance engine for one evidence record."""

    tenant_id: str
    case_id: str
    batch_id: str
    artifact_filename: str
    artifact_version: int
    artifact_sha256: str
    spec_id: str
    spec_version: str
    spec_sha256: str
    trend_baseline_id: str | None = None
    trend_baseline_version: str | None = None
    trend_baseline_sha256: str | None = None
    status: ComplianceStatus
    findings: list[ComplianceFinding] = Field(default_factory=list)
    root_cause_summary: dict[RootCauseCategory, int] = Field(
        default_factory=dict,
        description=(
            "Count of findings per root-cause dimension, computed from "
            "``findings`` — a quick-glance hypothesis-space size for the "
            "(future) Investigation agent, not itself a hypothesis."
        ),
    )
    source_evidence: ValidatedEvidence = Field(
        description=(
            "The full validated-evidence record this result was computed "
            "from, carried forward rather than referenced by id, so this "
            "result is independently auditable without a separate lookup."
        )
    )
    audit: ComplianceAuditEvent
    evaluated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    decision_authority: str = "none"
