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

"""Draft-only investigation and challenger schemas.

Nothing in these types is a batch disposition, a certified root cause, or a
Quality Unit decision. ``decision_authority`` is locked to ``none`` so a
schema-valid model response cannot claim otherwise.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.domain.case_models import CaseComplianceResult
from app.domain.master_data import RootCauseCategory


class CitedClaim(BaseModel):
    """One statement that must point at evidence already on the case."""

    claim: str
    source_uris: list[str] = Field(min_length=1)
    finding_ids: list[str] = Field(default_factory=list)


class RootCauseHypothesis(BaseModel):
    """A candidate cause. Never a determination."""

    category: RootCauseCategory
    statement: str
    supporting_claims: list[CitedClaim] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ProductImpactDraft(BaseModel):
    """Draft impact prose for a QP to review, not a safety determination."""

    statement: str
    supporting_claims: list[CitedClaim] = Field(min_length=1)
    patient_safety_touch: bool = False


class InvestigationDraft(BaseModel):
    """Investigation-agent output. Draft only."""

    tenant_id: str
    case_id: str
    batch_id: str
    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)
    product_impact: ProductImpactDraft | None = None
    open_questions: list[str] = Field(default_factory=list)
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Investigation drafts cannot claim decision authority.")
        return value


class ChallengerAttackType(StrEnum):
    UNSOURCED_CLAIM = "unsourced_claim"
    ALTERNATIVE_CAUSE = "alternative_cause"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    OVERCONFIDENT = "overconfident"


class ChallengerAttack(BaseModel):
    attack_type: ChallengerAttackType
    target: str
    detail: str


class ChallengerReport(BaseModel):
    """Attacks on the draft. Does not certify sufficiency."""

    attacks: list[ChallengerAttack] = Field(default_factory=list)
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Challenger reports cannot claim decision authority.")
        return value


class InvestigationDraftResult(BaseModel):
    """Envelope after the investigation node, carrying the joined case."""

    case: CaseComplianceResult
    skipped: bool = False
    skip_reason: str | None = None
    draft: InvestigationDraft | None = None
    decision_authority: str = "none"


class ChallengedInvestigation(BaseModel):
    case: CaseComplianceResult
    skipped: bool = False
    skip_reason: str | None = None
    draft: InvestigationDraft | None = None
    challenger: ChallengerReport | None = None
    decision_authority: str = "none"


class InvestigationGateResult(BaseModel):
    """Deterministic citation / readiness check. Not a disposition."""

    passed: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    decision_authority: str = "none"


class InvestigationCaseResult(BaseModel):
    """Workflow output through investigation, challenger, and the citation gate."""

    case: CaseComplianceResult
    draft: InvestigationDraft | None = None
    challenger: ChallengerReport | None = None
    gate: InvestigationGateResult
    decision_authority: str = "none"
