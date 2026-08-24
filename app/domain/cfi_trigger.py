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

"""Deterministic CFI (Cross-Functional Investigation) trigger.

``decide_cfi_required`` is a pure function over the joined case's own
investigation gate result and challenger report. No agent, and no field an
agent controls, decides whether a CFI is warranted — the same split already
enforced by ``investigation_gate.evaluate_investigation_gate`` for citation
sufficiency. Thresholds are change-controlled master data
(``master_data.load_cfi_trigger_spec``), never literals, so an auditor can
name the exact approved specification and its hash behind any trigger
decision.

Per GMPSOP QMS-130 section 4.2.1, whether a CFI is required is a QA-lead-or-
rule call, not an investigator's — this function is that rule.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.domain.cfi_models import CfiTriggerReason
from app.domain.investigation_models import (
    ChallengerAttackType,
    InvestigationCaseResult,
)
from app.domain.master_data import CfiTriggerSpec, load_cfi_trigger_spec


class CfiTriggerDecision(BaseModel):
    """Result of evaluating whether a CFI must open for this case.

    ``decision_authority`` stays ``"none"``: this is a routing decision
    over already-computed deterministic signals, not a new finding or a
    disposition.
    """

    required: bool
    reasons: list[CfiTriggerReason] = Field(default_factory=list)
    trigger_spec_version: str
    trigger_spec_sha256: str
    decision_authority: str = "none"
    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI trigger decisions cannot claim authority.")
        return value


def decide_cfi_required(
    result: InvestigationCaseResult,
    *,
    spec: CfiTriggerSpec | None = None,
) -> CfiTriggerDecision:
    """Evaluates GMPSOP QMS-130 section 4.2.1's CFI trigger conditions.

    Args:
        result: The joined case through ``gate_investigation`` — carries the
            investigation draft, challenger report, citation gate result,
            and the case's own retrieved prior-art facts.
        spec: Override for the loaded trigger spec (tests only). Production
            callers must omit this so the change-controlled, hashed spec on
            disk is what actually governs the decision.

    Returns:
        A ``CfiTriggerDecision`` naming every condition that fired. A CFI is
        required if ``reasons`` is non-empty.
    """
    active_spec = spec or load_cfi_trigger_spec()
    reasons: list[CfiTriggerReason] = []

    if not result.gate.passed:
        reasons.append(CfiTriggerReason.GATE_FAILED)

    draft = result.draft
    if draft is None or not draft.hypotheses:
        reasons.append(CfiTriggerReason.NO_HYPOTHESES)
    elif all(
        hypothesis.confidence < active_spec.low_confidence_threshold
        for hypothesis in draft.hypotheses
    ):
        reasons.append(CfiTriggerReason.LOW_CONFIDENCE)

    challenger = result.challenger
    if challenger is not None:
        attack_types = {attack.attack_type for attack in challenger.attacks}
        if ChallengerAttackType.INSUFFICIENT_EVIDENCE in attack_types:
            reasons.append(CfiTriggerReason.CHALLENGER_INSUFFICIENT_EVIDENCE)
        if ChallengerAttackType.ALTERNATIVE_CAUSE in attack_types:
            reasons.append(CfiTriggerReason.CHALLENGER_ALTERNATIVE_CAUSE)

    if (
        len(result.case.prior_art)
        >= active_spec.recurrence_prior_occurrences_threshold
    ):
        reasons.append(CfiTriggerReason.RECURRENCE)

    # Deduplicate while preserving order.
    unique: list[CfiTriggerReason] = []
    seen: set[CfiTriggerReason] = set()
    for reason in reasons:
        if reason not in seen:
            unique.append(reason)
            seen.add(reason)

    return CfiTriggerDecision(
        required=bool(unique),
        reasons=unique,
        trigger_spec_version=active_spec.spec_version,
        trigger_spec_sha256=active_spec.spec_sha256,
        decision_authority="none",
    )
