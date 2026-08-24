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

"""CFI hypothesis ledger: code-only state transitions.

An agent may propose a hypothesis (``CfiEnquiryPlan.new_hypothesis``) and
may state, in its own output text, that it believes a hypothesis is
confirmed or ruled out. Neither statement changes ``CfiHypothesis.state`` by
itself. Only the functions in this module do, and only by re-deriving the
state from ``CfiEvidenceItem`` records already captured on the
``CfiInvestigation`` through a real tool call — the same discipline
``investigation_gate.py`` already applies to citations.

Support/refutation rule for Phase 1 (deliberately simple and auditable):
a hypothesis becomes ``supported`` once at least
``_MIN_SUPPORTING_EVIDENCE`` evidence items are linked to it as supporting
claims and none are linked as refuting claims; ``refuted`` once at least one
refuting claim is linked and no supporting claim outweighs it. Both rules
require every cited ``evidence_id`` to name a real, existing
``CfiEvidenceItem`` on the same investigation — a claim citing an unknown
evidence id is rejected outright, mirroring
``investigation_gate.citation_failures``.
"""

from __future__ import annotations

from app.domain.cfi_models import (
    CfiCitedClaim,
    CfiEvidenceItem,
    CfiHypothesis,
    CfiHypothesisProposal,
    CfiHypothesisState,
    CfiInvestigation,
)

_MIN_SUPPORTING_EVIDENCE = 1


class CfiLedgerError(ValueError):
    """A ledger mutation was rejected — e.g. a claim cites unknown evidence."""


def _known_evidence_ids(investigation: CfiInvestigation) -> set[str]:
    return {item.evidence_id for item in investigation.evidence}


def _validate_claims(
    claims: list[CfiCitedClaim], known_evidence_ids: set[str]
) -> None:
    for claim in claims:
        for evidence_id in claim.evidence_ids:
            if evidence_id not in known_evidence_ids:
                raise CfiLedgerError(
                    f"Claim cites unknown evidence_id {evidence_id!r}."
                )


def open_hypothesis(
    investigation: CfiInvestigation,
    proposal: CfiHypothesisProposal,
    *,
    round_number: int,
) -> tuple[CfiInvestigation, CfiHypothesis]:
    """Accepts a lead-agent-proposed hypothesis into the ledger as ``open``.

    Assigns the next ``hypothesis_id`` sequence value (``h{n}``) from the
    count already persisted — deterministic and resume-safe, never an
    agent-supplied or randomly generated id (see ``CfiHypothesis`` and
    ``CfiHypothesisProposal`` docstrings).

    Returns:
        ``(updated_investigation, opened_hypothesis)`` — the caller needs
        the assigned id to reference this hypothesis in the same round.
    """
    next_id = f"h{len(investigation.hypotheses) + 1}"
    hypothesis = CfiHypothesis(
        hypothesis_id=next_id,
        category=proposal.category,
        statement=proposal.statement,
        opened_in_round=round_number,
    )
    updated_hypotheses = [*investigation.hypotheses, hypothesis]
    updated = investigation.model_copy(update={"hypotheses": updated_hypotheses})
    return updated, hypothesis


def record_evidence(
    investigation: CfiInvestigation,
    *,
    round_number: int,
    source_tool: str,
    source_uri: str,
    fact: str,
) -> tuple[CfiInvestigation, CfiEvidenceItem]:
    """Appends one evidence item captured from a real tool call.

    Assigns the next ``evidence_id`` sequence value (``e{n}``), the same
    resume-safe allocation discipline as ``open_hypothesis``. This is the
    only way an evidence_id becomes citeable by a later claim — there is no
    path for a claim to cite evidence that was not first recorded here.

    Returns:
        ``(updated_investigation, recorded_evidence)``.
    """
    next_id = f"e{len(investigation.evidence) + 1}"
    evidence = CfiEvidenceItem(
        evidence_id=next_id,
        round_number=round_number,
        source_tool=source_tool,
        source_uri=source_uri,
        fact=fact,
    )
    updated_evidence = [*investigation.evidence, evidence]
    updated = investigation.model_copy(update={"evidence": updated_evidence})
    return updated, evidence


def _settle_one(
    hypothesis: CfiHypothesis,
    *,
    supporting_claims: list[CfiCitedClaim],
    refuting_claims: list[CfiCitedClaim],
    known_evidence_ids: set[str],
    now,
    budget_exhausted: bool,
) -> CfiHypothesis:
    if hypothesis.state is not CfiHypothesisState.OPEN:
        return hypothesis

    _validate_claims(supporting_claims, known_evidence_ids)
    _validate_claims(refuting_claims, known_evidence_ids)

    merged_supporting = [*hypothesis.supporting_claims, *supporting_claims]
    merged_refuting = [*hypothesis.refuting_claims, *refuting_claims]

    if merged_refuting:
        new_state = CfiHypothesisState.REFUTED
    elif len(merged_supporting) >= _MIN_SUPPORTING_EVIDENCE:
        new_state = CfiHypothesisState.SUPPORTED
    elif budget_exhausted:
        new_state = CfiHypothesisState.UNDETERMINED
    else:
        return hypothesis.model_copy(
            update={
                "supporting_claims": merged_supporting,
                "refuting_claims": merged_refuting,
            }
        )

    return hypothesis.model_copy(
        update={
            "state": new_state,
            "supporting_claims": merged_supporting,
            "refuting_claims": merged_refuting,
            "settled_at": now,
        }
    )


def settle_hypotheses(
    investigation: CfiInvestigation,
    *,
    claims_by_hypothesis_id: dict[
        str, tuple[list[CfiCitedClaim], list[CfiCitedClaim]]
    ],
    now,
    budget_exhausted: bool = False,
) -> CfiInvestigation:
    """Re-derives hypothesis states from newly captured evidence claims.

    Args:
        investigation: The current CFI record. Evidence referenced by
            ``claims_by_hypothesis_id`` must already exist on
            ``investigation.evidence`` (via ``record_evidence``).
        claims_by_hypothesis_id: For each hypothesis touched this round, the
            ``(supporting_claims, refuting_claims)`` pair to merge in.
        now: Timestamp to stamp on any hypothesis that settles this call.
        budget_exhausted: When True, any hypothesis that is still ``open``
            after merging its claims and has no refuting or sufficient
            supporting evidence settles as ``undetermined`` rather than
            remaining open indefinitely.

    Returns:
        A new ``CfiInvestigation`` with updated hypothesis states. Never
        mutates the input in place.
    """
    known_evidence_ids = _known_evidence_ids(investigation)
    updated_hypotheses: list[CfiHypothesis] = []
    for hypothesis in investigation.hypotheses:
        supporting, refuting = claims_by_hypothesis_id.get(
            hypothesis.hypothesis_id, ([], [])
        )
        updated_hypotheses.append(
            _settle_one(
                hypothesis,
                supporting_claims=supporting,
                refuting_claims=refuting,
                known_evidence_ids=known_evidence_ids,
                now=now,
                budget_exhausted=budget_exhausted,
            )
        )
    return investigation.model_copy(update={"hypotheses": updated_hypotheses})


def force_undetermined_on_budget_exhaustion(
    investigation: CfiInvestigation, *, now
) -> CfiInvestigation:
    """Settles every still-``open`` hypothesis as ``undetermined``.

    Called by ``cfi_gate`` when the budget is exhausted and hypotheses
    remain open with neither sufficient support nor any refutation — the
    honest ``cfi_abandoned_budget_exhausted``/``root_cause_undetermined``
    path never leaves a hypothesis silently stuck at ``open``.
    """
    updated_hypotheses = [
        hypothesis.model_copy(
            update={"state": CfiHypothesisState.UNDETERMINED, "settled_at": now}
        )
        if hypothesis.state is CfiHypothesisState.OPEN
        else hypothesis
        for hypothesis in investigation.hypotheses
    ]
    return investigation.model_copy(update={"hypotheses": updated_hypotheses})
