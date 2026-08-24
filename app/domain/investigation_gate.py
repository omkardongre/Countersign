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

"""Deterministic evidence-sufficiency gate after Investigation + Challenger.

The models may draft hypotheses. This module decides whether those drafts are
citeable enough to remain on the case. It never releases a batch.
"""

from __future__ import annotations

from google.adk.workflow import node

from app.domain.case_models import CaseComplianceResult
from app.domain.department_models import BranchStatus
from app.domain.guidance_source import sop_corpus_configured
from app.domain.investigation_brief import allowed_finding_ids, allowed_source_uris
from app.domain.investigation_models import (
    ChallengedInvestigation,
    CitedClaim,
    InvestigationCaseResult,
    InvestigationDraft,
    InvestigationGateResult,
)


def departments_ready(case: CaseComplianceResult) -> list[str]:
    """Investigation may run once every department actually returned data.

    OOS/OOT/completeness findings are the investigation's input, not a reason
    to skip it. Only a failed collector (no snapshot, MIME refusal, crash)
    blocks the LLM path.
    """
    reasons: list[str] = []
    if any(branch.status is BranchStatus.FAILED for branch in case.branches):
        reasons.append("One or more department branches failed.")
    return reasons


def _iter_claims(draft: InvestigationDraft) -> list[CitedClaim]:
    claims: list[CitedClaim] = []
    for hypothesis in draft.hypotheses:
        claims.extend(hypothesis.supporting_claims)
    if draft.product_impact is not None:
        claims.extend(draft.product_impact.supporting_claims)
    return claims


def _controlled_sop_uris(case: CaseComplianceResult) -> set[str]:
    """In-force controlled-SOP source URIs only — never public guidance.

    Doc 12 T1-5: public guidance is advisory context and must never satisfy
    the mandatory-controlled-SOP-citation requirement, so this is a
    deliberately narrower set than ``allowed_source_uris``.
    """
    return {
        citation.source_uri
        for citation in case.controlled_guidance
        if citation.in_force and citation.corpus.value == "controlled_sop"
    }


def guidance_citation_failures(
    case: CaseComplianceResult, draft: InvestigationDraft
) -> list[str]:
    """Doc 12 T1-5: at least one claim must cite an in-force controlled SOP.

    Enforced only when this deployment actually has a controlled-SOP corpus
    configured (``sop_corpus_configured``) — a deployment that has not yet
    provisioned T1-5 must not have every investigation start failing the
    gate on a requirement it cannot possibly meet. Once configured, this is
    unconditional: a case with zero in-force SOP citations available, or
    whose draft never actually cited one, blocks the gate either way. A
    citation to a stale or unapproved SOP source_uri does not count,
    because that URI is not in ``allowed_source_uris`` in the first place
    (see ``investigation_brief.allowed_source_uris``) and so is already
    caught as an unknown-source-uri citation failure.
    """
    if not sop_corpus_configured():
        return []
    sop_uris = _controlled_sop_uris(case)
    if not sop_uris:
        return [
            "No in-force controlled SOP citation is available for this case."
        ]
    cited_uris = {
        uri for claim in _iter_claims(draft) for uri in claim.source_uris
    }
    if not (cited_uris & sop_uris):
        return [
            "Investigation draft cites no in-force controlled SOP source."
        ]
    return []


def citation_failures(case: CaseComplianceResult, draft: InvestigationDraft) -> list[str]:
    reasons: list[str] = []
    if (
        draft.tenant_id != case.classified.tenant_id
        or draft.case_id != case.classified.case_id
        or draft.batch_id != case.classified.batch_id
    ):
        reasons.append("Investigation draft scope does not match the classified case.")
    if draft.decision_authority != "none":
        reasons.append("Investigation draft claimed decision authority.")
    if not draft.hypotheses:
        reasons.append("Investigation draft contains no cited hypotheses.")

    uris = allowed_source_uris(case)
    finding_ids = allowed_finding_ids(case)
    for claim in _iter_claims(draft):
        for uri in claim.source_uris:
            if uri not in uris:
                reasons.append(f"Claim cites unknown source_uri {uri!r}.")
        for finding_id in claim.finding_ids:
            if finding_id not in finding_ids:
                reasons.append(f"Claim cites unknown finding_id {finding_id!r}.")
    return reasons


def evaluate_investigation_gate(
    challenged: ChallengedInvestigation,
) -> InvestigationGateResult:
    reasons: list[str] = []
    if challenged.skipped or challenged.draft is None:
        reasons.append(challenged.skip_reason or "Investigation was skipped.")
        return InvestigationGateResult(passed=False, blocked_reasons=reasons)

    reasons.extend(departments_ready(challenged.case))
    reasons.extend(citation_failures(challenged.case, challenged.draft))
    reasons.extend(guidance_citation_failures(challenged.case, challenged.draft))

    if challenged.challenger is None:
        reasons.append("Challenger did not produce a report.")
    elif challenged.challenger.decision_authority != "none":
        reasons.append("Challenger report claimed decision authority.")
    # Challenger attacks are carried for the QP. They do not fail the gate:
    # an LLM cannot veto a draft whose citations already check out.

    # Deduplicate while preserving order.
    unique: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason not in seen:
            unique.append(reason)
            seen.add(reason)
    return InvestigationGateResult(passed=not unique, blocked_reasons=unique)


@node(name="gate_investigation")
def gate_investigation(node_input: ChallengedInvestigation) -> InvestigationCaseResult:
    challenged = (
        node_input
        if isinstance(node_input, ChallengedInvestigation)
        else ChallengedInvestigation.model_validate(node_input)
    )
    gate = evaluate_investigation_gate(challenged)
    return InvestigationCaseResult(
        case=challenged.case,
        draft=challenged.draft,
        challenger=challenged.challenger,
        gate=gate,
        decision_authority="none",
    )
