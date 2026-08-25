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

"""Cross-Functional Investigation (CFI) domain models.

A CFI is a separate, durable investigation record opened when the joined
case's own investigation could not establish a cited root cause per
GMPSOP QMS-130. It is never a batch disposition and never a QP record:
every model-produced object here carries ``decision_authority = "none"``,
the same lock already used throughout ``investigation_models.py`` and
``models.py``.

The hypothesis ledger's state machine lives in ``cfi_ledger.py`` — these
types are the data shape only. No field here grants an agent authority to
move a hypothesis between states; that authority belongs to deterministic
code alone (see ``CfiHypothesis.state`` docstring).
"""

from __future__ import annotations

import datetime
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.domain.cfi_interview_models import CfiInterviewResult
from app.domain.master_data import RootCauseCategory


class CfiTriggerReason(StrEnum):
    """Which GMPSOP QMS-130 section 4.2.1 condition fired.

    Each member corresponds to one row of the trigger table in
    ``13_cfi_extended_investigation_plan.md`` section 3. Recorded, never
    inferred after the fact — ``decide_cfi_required`` in ``cfi_trigger.py``
    returns the exact reason(s) that fired alongside its boolean decision.
    """

    GATE_FAILED = "gate_failed"
    NO_HYPOTHESES = "no_hypotheses"
    LOW_CONFIDENCE = "low_confidence"
    CHALLENGER_INSUFFICIENT_EVIDENCE = "challenger_insufficient_evidence"
    CHALLENGER_ALTERNATIVE_CAUSE = "challenger_alternative_cause"
    RECURRENCE = "recurrence"


class CfiHypothesisState(StrEnum):
    """Ledger state. Transitions are code-only — see ``cfi_ledger.py``."""

    OPEN = "open"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    UNDETERMINED = "undetermined"


class CfiEnquiryKind(StrEnum):
    """The line of enquiry a planning round may dispatch to.

    Phase 1 implements only ``SCOPE_EXPANSION``. The remaining members are
    declared now so ``CfiEnquiryPlan.kind`` and the dispatch routing table
    in ``cfi_graph.py`` do not need a breaking schema change when Phase 2/3
    land ``INTERVIEW``/``TREND_CORRELATION``.
    """

    SCOPE_EXPANSION = "scope_expansion"
    INTERVIEW = "interview"
    TREND_CORRELATION = "trend_correlation"


class CfiOutcome(StrEnum):
    """Terminal disposition of a CFI record. Never a batch disposition."""

    ROOT_CAUSE_IDENTIFIED = "root_cause_identified"
    ROOT_CAUSE_UNDETERMINED = "root_cause_undetermined"
    ABANDONED_BUDGET_EXHAUSTED = "cfi_abandoned_budget_exhausted"


class CfiCitedClaim(BaseModel):
    """One statement that must point at evidence already captured on the CFI.

    Mirrors ``investigation_models.CitedClaim``: a claim is citeable only
    because it names a real ``CfiEvidenceItem.evidence_id`` captured from an
    actual tool call, never because an agent asserted it.
    """

    claim: str
    evidence_ids: list[str] = Field(min_length=1)


class CfiHypothesis(BaseModel):
    """A candidate cause under investigation. Never a determination.

    ``hypothesis_id`` is assigned by ``cfi_ledger.open_hypothesis`` as a
    sequence number scoped to the CFI (``h1``, ``h2``, ...), derived from
    how many hypotheses are already persisted — never a random id an agent
    could regenerate differently on an ADK resume-triggered rerun. This
    keeps hypothesis identity resume-safe: reloading the durable
    ``CfiInvestigation`` and re-deriving the next sequence number after a
    crash never collides with, or silently duplicates, an already-persisted
    hypothesis.

    ``state`` starts at ``open`` when the lead agent proposes it and the
    ledger accepts it. Only ``cfi_ledger.settle_hypotheses`` may move it to
    ``supported``, ``refuted``, or ``undetermined`` — and only from
    evidence already captured in ``CfiInvestigation.evidence``. Setting
    this field directly, or an agent asserting a different state in prose,
    changes nothing: every ledger mutation path re-validates the evidence
    before writing a new state.
    """

    hypothesis_id: str
    category: RootCauseCategory
    statement: str
    state: CfiHypothesisState = CfiHypothesisState.OPEN
    opened_in_round: int = Field(ge=1)
    supporting_claims: list[CfiCitedClaim] = Field(default_factory=list)
    refuting_claims: list[CfiCitedClaim] = Field(default_factory=list)
    settled_at: datetime.datetime | None = None


class CfiEvidenceItem(BaseModel):
    """One fact captured from a real tool call during a CFI round.

    ``evidence_id`` is a sequence number (``e1``, ``e2``, ...) assigned by
    ``cfi_ledger.record_evidence`` from how many evidence items are already
    persisted, for the same resume-safety reason as
    ``CfiHypothesis.hypothesis_id``.

    ``source_uri``/``source_tool`` follow the same discipline as
    ``CaseMemoryFact.source_uri`` (``case_memory_models.py``): a fact is
    citeable only because it was captured from a real call, and this
    record is that capture, not the agent's paraphrase of it.
    """

    evidence_id: str
    round_number: int = Field(ge=1)
    source_tool: str
    source_uri: str
    fact: str
    captured_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )


class CfiHypothesisProposal(BaseModel):
    """A brand-new hypothesis as the lead agent may propose it.

    Deliberately lacks ``hypothesis_id``/``state``/round metadata: assigning
    a ledger-scoped sequence id and the initial ``open`` state is
    ``cfi_ledger.open_hypothesis``'s job, not the agent's — an agent-chosen
    identifier could collide or diverge across an ADK resume-triggered
    rerun of the planning node.
    """

    category: RootCauseCategory
    statement: str


class CfiEnquiryPlan(BaseModel):
    """Lead agent's typed output for one planning round. A proposal only.

    ``decision_authority`` is locked to ``"none"``: this plan chooses what
    to look at next. It does not, by itself, move any hypothesis, dispatch
    any tool call, or close the CFI — ``cfi_graph.py``'s deterministic
    dispatch node validates ``kind``/``target_hypothesis_id`` against the
    ledger before any tool runs.
    """

    kind: CfiEnquiryKind
    target_hypothesis_id: str | None = Field(
        default=None,
        description=(
            "Existing hypothesis this enquiry investigates, or None when "
            "the round proposes a brand-new hypothesis instead."
        ),
    )
    new_hypothesis: CfiHypothesisProposal | None = None
    rationale: str = Field(min_length=1)
    stop: bool = Field(
        default=False,
        description=(
            "Lead agent's recommendation to stop planning. Advisory only: "
            "``cfi_continue_gate`` decides whether the CFI actually "
            "continues, based on ledger settlement state and budget, not "
            "on this flag alone."
        ),
    )
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI enquiry plans cannot claim decision authority.")
        return value


class SiblingBatchCandidate(BaseModel):
    """One sibling batch returned by ``sibling_batch_query``.

    An association only, per Phase 1 scope: this never asserts causation,
    only that the named batch shares the queried attribute with the CFI's
    own batch.
    """

    batch_id: str
    shared_attribute: str
    shared_value: str
    source_uri: str


class SiblingBatchQueryResult(BaseModel):
    """Wraps the ``sibling_batch_query`` candidate list as a dict payload.

    MCP structured content must be a JSON object, not a bare array — the
    same reason ``CaseMemoryQueryResult`` wraps its own fact list — so this
    is the tool's actual return type, not ``list[SiblingBatchCandidate]``
    directly.
    """

    tenant_id: str
    batch_id: str
    shared_attribute: str
    candidates: list[SiblingBatchCandidate] = Field(default_factory=list)


class CfiBudgetState(BaseModel):
    """Consumed-so-far counters, checked against ``CfiBudgetSpec`` caps.

    Persisted alongside the CFI record so budget survives process restarts
    and Runtime resumes — the same reason ``sterility_incubation`` persists
    its clock in Cloud SQL rather than in ADK session state alone.
    """

    planning_rounds_used: int = 0
    tool_calls_in_current_round: int = Field(default=0, ge=0)
    budget_spec_version: str
    budget_spec_sha256: str


class CfiRoundState(BaseModel):
    """Internal graph-plumbing type threaded through the CFI loop's nodes.

    Not a durable record and never returned outside ``cfi_graph.py`` — the
    durable, auditable record is ``CfiInvestigation`` alone, persisted at
    every round boundary by ``cfi_continue_gate``. This type only carries
    what one round's deterministic nodes need to pass to the next:

    * ``active_hypothesis_id``: which hypothesis this round's dispatched
      enquiry investigates, set by ``cfi_plan_round`` after validating the
      lead agent's plan against the ledger.
    * ``should_dispatch``: whether ``dispatch_enquiry`` should route to an
      enquiry agent at all this round, or skip straight to settlement (an
      invalid/unsupported plan consumes a planning round but gathers no
      evidence, per bounded-autonomy guardrail 6 — never a crash).
    * ``pending_sibling_result``: the scope-expansion tool's captured,
      already-typed result for this round, consumed and cleared by
      ``record_evidence``.
    """

    investigation: CfiInvestigation
    round_number: int = Field(ge=1)
    active_hypothesis_id: str | None = None
    enquiry_kind: CfiEnquiryKind | None = None
    should_dispatch: bool = False
    rationale: str = ""
    pending_interview_result: CfiInterviewResult | None = None
    pending_sibling_result: SiblingBatchQueryResult | None = None


class CfiEscalationRequest(BaseModel):
    """``build_cfi_workflow()``'s ``START`` input: a pointer, not a payload.

    The CFI graph is deployed as its own Agent Runtime (CFI plan section
    4.1), reached only after ``escalate_to_cfi`` has already created and
    persisted the seed ``CfiInvestigation`` in Cloud SQL. This request
    therefore carries only the id and its authenticated tenant binding —
    the store, not this request object, is the single source of truth for
    the CFI's actual content.
    """

    cfi_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)


class CfiInvestigation(BaseModel):
    """The full durable CFI record. Never a batch disposition or QP record.

    ``decision_authority`` stays ``"none"`` on every machine-produced
    instance, mirroring ``InvestigationDraft``/``ChallengerReport``. Closure
    (``outcome`` becoming non-``None``) is written only by
    ``cfi_gate.evaluate_cfi_closure``, never by an agent.
    """

    cfi_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    revision: int = Field(default=0, ge=0)
    tenant_id: str = Field(min_length=1)
    source_case_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    trigger_reasons: list[CfiTriggerReason] = Field(min_length=1)
    trigger_spec_version: str
    trigger_spec_sha256: str
    opened_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    hypotheses: list[CfiHypothesis] = Field(default_factory=list)
    evidence: list[CfiEvidenceItem] = Field(default_factory=list)
    budget: CfiBudgetState
    outcome: CfiOutcome | None = None
    closed_at: datetime.datetime | None = None
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI investigations cannot claim decision authority.")
        return value
