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

"""The CFI (Cross-Functional Investigation) graph. Its own ADK ``Workflow``,
deployed as its own Agent Runtime — never a branch of ``case_graph.py``
(``13_cfi_extended_investigation_plan.md`` section 4.1).

    START -> load_cfi
          -> cfi_plan_round          AGENT: choose the next enquiry   ◀─┐
          -> dispatch_enquiry        deterministic: validate the plan │
          -> scope_expansion_agent   AGENT: sibling-batch tool call   │
          -> record_evidence         deterministic: capture, persist │
          -> settle_hypotheses_node  deterministic: confirm/refute    │
          -> cfi_continue_gate_node  deterministic: budget + settle ──┘ back-edge
          -> cfi_closure_node        deterministic: outcome, persist, done

Every node between ``dispatch_enquiry`` and ``cfi_continue_gate_node`` is a
single planning round. The back-edge from ``cfi_continue_gate_node`` to
``cfi_plan_round`` is the architectural point: the number of rounds is not
known when the graph starts, bounded only by ``CfiBudgetSpec`` (never by a
hard-coded loop count) — enforced in ``cfi_gate.cfi_continue_gate``, not in
a prompt.

Phase 1 dispatches only ``CfiEnquiryKind.SCOPE_EXPANSION``. ``dispatch_
enquiry`` uses ADK's routing-map back-edge (``google.adk.workflow``'s
``route``/``DEFAULT_ROUTE`` mechanism, per the installed ``google-adk==2.6.3``
API) rather than the newer graph-route documentation literally, since that
is the API this repository's dependency actually ships.

Every node persists the durable ``CfiInvestigation`` to Cloud SQL (via
``McpCfiStore``) at its own boundary — the CFI's true state lives in the
store, not only in ADK session state, so a crash mid-round loses at most
the in-flight round, never the whole investigation (same durability
posture as ``sterility_incubation``).
"""

from __future__ import annotations

import datetime
import json

from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.workflow import DEFAULT_ROUTE, START, Workflow, node

from app.domain.cfi_gate import cfi_continue_gate, evaluate_cfi_closure
from app.domain.cfi_lead import build_cfi_lead_content, get_cfi_lead_agent
from app.domain.cfi_ledger import open_hypothesis
from app.domain.cfi_ledger import record_evidence as ledger_record_evidence
from app.domain.cfi_ledger import settle_hypotheses as ledger_settle_hypotheses
from app.domain.cfi_models import (
    CfiCitedClaim,
    CfiEnquiryKind,
    CfiEnquiryPlan,
    CfiEscalationRequest,
    CfiHypothesisProposal,
    CfiInvestigation,
    CfiRoundState,
)
from app.domain.cfi_scope import (
    build_scope_expansion_content,
    get_scope_expansion_agent,
)
from app.domain.cfi_scope import (
    make_sibling_batch_tool as _make_sibling_batch_tool,
)
from app.domain.cfi_store_mcp import McpCfiStore
from app.domain.master_data import load_cfi_budget_spec

CFI_WORKFLOW_NAME = "cfi_investigation"
CFI_WORKFLOW_VERSION = "cfi_investigation.1"

# Route values for dispatch_enquiry's routing-map edge. Distinct from
# CfiEnquiryKind values so a change to the enquiry-kind vocabulary (Phase 2/3
# adding "interview"/"trend_correlation") does not silently change graph
# routing semantics without a deliberate edit here too.
_ROUTE_SCOPE_EXPANSION = "scope_expansion"
_ROUTE_SKIP_TO_SETTLEMENT = "skip"


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@node(name="load_cfi", rerun_on_resume=True)
async def load_cfi(node_input: CfiEscalationRequest) -> CfiRoundState:
    """Loads the durable CFI record ``escalate_to_cfi`` already persisted.

    The seed record and its budget/trigger spec hashes were written by the
    case-graph handoff (``app/domain/cfi_escalation.py``); this node never
    creates a CFI, only resumes one.
    """
    request = (
        node_input
        if isinstance(node_input, CfiEscalationRequest)
        else CfiEscalationRequest.model_validate(node_input)
    )
    investigation = await McpCfiStore().get_async(
        cfi_id=request.cfi_id, tenant_id=request.tenant_id
    )
    if investigation is None:
        raise ValueError(f"No CFI record found for cfi_id={request.cfi_id!r}.")
    if investigation.outcome is not None:
        raise ValueError(
            f"CFI {request.cfi_id!r} is already closed with outcome "
            f"{investigation.outcome.value!r}; refusing to resume planning."
        )
    return CfiRoundState(
        investigation=investigation,
        round_number=investigation.budget.planning_rounds_used + 1,
    )


@node(name="cfi_plan_round", rerun_on_resume=True)
async def cfi_plan_round(ctx: Context, node_input: CfiRoundState) -> Event:
    """AGENT: chooses the next line of enquiry from the ledger's own state.

    Validates the agent's plan deterministically before it can affect
    anything: an out-of-vocabulary ``kind``, a ``target_hypothesis_id`` that
    does not name an existing ``open`` hypothesis, or a duplicate proposed
    hypothesis is rejected here and the round proceeds with
    ``should_dispatch=False`` — consuming one planning round without ever
    reaching a tool call, never crashing the graph (guardrail 6/8).
    """
    state = (
        node_input
        if isinstance(node_input, CfiRoundState)
        else CfiRoundState.model_validate(node_input)
    )
    investigation = state.investigation
    content = build_cfi_lead_content(investigation)
    raw = await ctx.run_node(get_cfi_lead_agent(), node_input=content)
    if raw is None:
        return Event(
            output=state.model_copy(
                update={"should_dispatch": False, "rationale": "Lead agent returned no plan."}
            ).model_dump(mode="json")
        )
    plan = CfiEnquiryPlan.model_validate(
        raw if isinstance(raw, (dict, CfiEnquiryPlan)) else json.loads(raw)
    )

    open_ids = {
        h.hypothesis_id for h in investigation.hypotheses if h.state.value == "open"
    }
    existing_statements = {h.statement.strip().lower() for h in investigation.hypotheses}

    active_hypothesis_id: str | None = None
    should_dispatch = False
    updated_investigation = investigation

    if plan.kind is not CfiEnquiryKind.SCOPE_EXPANSION:
        rationale = (
            f"Lead agent proposed unsupported enquiry kind {plan.kind.value!r} "
            "in Phase 1; skipping to settlement this round."
        )
    elif plan.target_hypothesis_id is not None:
        if plan.target_hypothesis_id in open_ids:
            active_hypothesis_id = plan.target_hypothesis_id
            should_dispatch = True
            rationale = plan.rationale
        else:
            rationale = (
                f"Lead agent named target_hypothesis_id "
                f"{plan.target_hypothesis_id!r}, which is not an open "
                "hypothesis on this CFI; skipping to settlement this round."
            )
    elif plan.new_hypothesis is not None:
        proposal: CfiHypothesisProposal = plan.new_hypothesis
        if proposal.statement.strip().lower() in existing_statements:
            rationale = (
                "Lead agent proposed a hypothesis duplicating an existing "
                "one; skipping to settlement this round."
            )
        else:
            updated_investigation, opened = open_hypothesis(
                investigation, proposal, round_number=state.round_number
            )
            active_hypothesis_id = opened.hypothesis_id
            should_dispatch = True
            rationale = plan.rationale
    else:
        rationale = (
            "Lead agent's plan named neither an existing hypothesis nor a "
            "new one; skipping to settlement this round."
        )

    new_state = state.model_copy(
        update={
            "investigation": updated_investigation,
            "active_hypothesis_id": active_hypothesis_id,
            "should_dispatch": should_dispatch,
            "rationale": rationale,
        }
    )
    return Event(output=new_state.model_dump(mode="json"))


@node(name="dispatch_enquiry", rerun_on_resume=True)
async def dispatch_enquiry(ctx: Context, node_input: CfiRoundState) -> Event:
    """Deterministic route: to the scope-expansion agent, or straight past
    it to settlement when ``cfi_plan_round`` rejected the round's plan.
    """
    state = (
        node_input
        if isinstance(node_input, CfiRoundState)
        else CfiRoundState.model_validate(node_input)
    )
    ctx.route = (
        _ROUTE_SCOPE_EXPANSION if state.should_dispatch else _ROUTE_SKIP_TO_SETTLEMENT
    )
    return Event(output=state.model_dump(mode="json"))


@node(name="run_scope_expansion", rerun_on_resume=True)
async def run_scope_expansion(ctx: Context, node_input: CfiRoundState) -> Event:
    """AGENT: calls ``sibling_batch_query`` for the round's active hypothesis.

    The tenant/batch scope is bound into the tool closure from the CFI's
    own record, never supplied by the model — same discipline as every
    other tool closure in this codebase.
    """
    state = (
        node_input
        if isinstance(node_input, CfiRoundState)
        else CfiRoundState.model_validate(node_input)
    )
    investigation = state.investigation
    captured: dict = {}
    sibling_batch_tool = _make_sibling_batch_tool(investigation, captured)

    content = build_scope_expansion_content(state.rationale)
    await ctx.run_node(
        get_scope_expansion_agent(sibling_batch_tool), node_input=content
    )
    pending = captured.get("result")
    tool_calls_made = captured.get("tool_calls", 0)
    updated_investigation = investigation.model_copy(
        update={
            "budget": investigation.budget.model_copy(
                update={
                    "tool_calls_in_current_round": (
                        investigation.budget.tool_calls_in_current_round
                        + tool_calls_made
                    )
                }
            )
        }
    )
    new_state = state.model_copy(
        update={
            "investigation": updated_investigation,
            "pending_sibling_result": pending,
        }
    )
    return Event(output=new_state.model_dump(mode="json"))


@node(name="record_evidence", rerun_on_resume=True)
async def record_evidence(node_input: CfiRoundState) -> Event:
    """Deterministic: captures the enquiry's tool result as evidence.

    Nothing here trusts the agent's own prose. Only ``state.pending_
    sibling_result`` — the tool's captured, typed return value — becomes
    evidence. A round that dispatched but got an empty result (no siblings
    found) still records that fact: "no siblings found" is itself evidence
    for settlement, not silently discarded.
    """
    state = (
        node_input
        if isinstance(node_input, CfiRoundState)
        else CfiRoundState.model_validate(node_input)
    )
    investigation = state.investigation
    result = state.pending_sibling_result

    if result is not None and state.active_hypothesis_id is not None:
        if result.candidates:
            fact = (
                f"{len(result.candidates)} sibling batch(es) share "
                f"{result.shared_attribute}={result.candidates[0].shared_value!r} "
                f"with {result.batch_id}: "
                f"{', '.join(c.batch_id for c in result.candidates)}."
            )
            source_uri = result.candidates[0].source_uri
        else:
            fact = (
                f"No sibling batches on tenant {result.tenant_id} share "
                f"{result.shared_attribute} with {result.batch_id}."
            )
            source_uri = f"sibling_batch_query://{result.tenant_id}/{result.batch_id}"
        investigation, evidence = ledger_record_evidence(
            investigation,
            round_number=state.round_number,
            source_tool="sibling_batch_query",
            source_uri=source_uri,
            fact=fact,
        )
        claims = {
            state.active_hypothesis_id: (
                (
                    [CfiCitedClaim(claim=fact, evidence_ids=[evidence.evidence_id])],
                    [],
                )
                if result.candidates
                else ([], [CfiCitedClaim(claim=fact, evidence_ids=[evidence.evidence_id])])
            )
        }
        investigation = ledger_settle_hypotheses(
            investigation, claims_by_hypothesis_id=claims, now=_utcnow()
        )

    new_state = state.model_copy(
        update={"investigation": investigation, "pending_sibling_result": None}
    )
    return Event(output=new_state.model_dump(mode="json"))


@node(name="cfi_continue_gate_node", rerun_on_resume=True)
async def cfi_continue_gate_node(node_input: CfiRoundState) -> Event:
    """Deterministic: budget + settlement check, then the loop's back-edge.

    Persists the round's ``CfiInvestigation`` to Cloud SQL before deciding
    the route, so the durable record always reflects at least this round's
    work even if the process crashes immediately after.
    """
    state = (
        node_input
        if isinstance(node_input, CfiRoundState)
        else CfiRoundState.model_validate(node_input)
    )
    investigation = state.investigation.model_copy(
        update={
            "budget": state.investigation.budget.model_copy(
                update={
                    "planning_rounds_used": (
                        state.investigation.budget.planning_rounds_used + 1
                    )
                }
            )
        }
    )
    should_continue, investigation = cfi_continue_gate(
        investigation, spec=load_cfi_budget_spec()
    )
    if should_continue:
        # Next round starts a fresh tool-call allowance; the cap is
        # per-round, not cumulative across the whole CFI.
        investigation = investigation.model_copy(
            update={
                "budget": investigation.budget.model_copy(
                    update={"tool_calls_in_current_round": 0}
                )
            }
        )
    saved = await McpCfiStore().save_async(investigation)

    new_state = CfiRoundState(
        investigation=saved,
        round_number=state.round_number + 1,
    )
    return Event(
        output=new_state.model_dump(mode="json"),
        actions={"route": "continue" if should_continue else "close"},
    )


@node(name="cfi_closure_node", rerun_on_resume=True)
async def cfi_closure_node(node_input: CfiRoundState) -> CfiInvestigation:
    """Deterministic: final outcome, persisted, terminal node."""
    state = (
        node_input
        if isinstance(node_input, CfiRoundState)
        else CfiRoundState.model_validate(node_input)
    )
    closed = evaluate_cfi_closure(
        state.investigation, spec=load_cfi_budget_spec()
    )
    return await McpCfiStore().save_async(closed)


def build_cfi_workflow() -> Workflow:
    """Builds the CFI investigation Workflow.

    A separate ``Workflow`` from ``build_deviation_case_workflow()``, per
    CFI plan section 4.1: this graph has a genuinely different shape (an
    unbounded, budget-capped loop) and is intended for its own Agent
    Runtime with its own Agent Identity/Gateway allow-list.
    """
    return Workflow(
        name=CFI_WORKFLOW_NAME,
        input_schema=CfiEscalationRequest,
        output_schema=CfiInvestigation,
        edges=[
            (START, load_cfi),
            (load_cfi, cfi_plan_round),
            (cfi_plan_round, dispatch_enquiry),
            (
                dispatch_enquiry,
                {
                    _ROUTE_SCOPE_EXPANSION: run_scope_expansion,
                    DEFAULT_ROUTE: record_evidence,
                },
            ),
            (run_scope_expansion, record_evidence),
            (record_evidence, cfi_continue_gate_node),
            (
                cfi_continue_gate_node,
                {
                    "continue": cfi_plan_round,
                    "close": cfi_closure_node,
                },
            ),
        ],
    )
