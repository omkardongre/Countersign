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

"""CFI continuation and closure gates. Deterministic; never agent-decided.

``cfi_continue_gate`` decides, after every planning round, whether the CFI
graph's back-edge fires again or the CFI moves to closure — driven by the
ledger's own settlement state and the change-controlled budget
(``master_data.load_cfi_budget_spec``), never by the lead agent's ``stop``
recommendation alone (``CfiEnquiryPlan.stop`` is advisory only, per its own
docstring).

``cfi_closure_gate`` decides the terminal ``CfiOutcome`` once continuation
stops. Per GMPSOP QMS-130 section 5.9, "root cause cannot be identified" is
a legitimate outcome, not a failure state — it is escalated to the site
quality review team, not hidden.
"""

from __future__ import annotations

import datetime

from app.domain.cfi_ledger import force_undetermined_on_budget_exhaustion
from app.domain.cfi_models import (
    CfiBudgetState,
    CfiHypothesisState,
    CfiInvestigation,
    CfiOutcome,
)
from app.domain.master_data import CfiBudgetSpec, load_cfi_budget_spec


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def budget_exhausted(
    budget: CfiBudgetState,
    *,
    opened_at: datetime.datetime,
    spec: CfiBudgetSpec,
    now: datetime.datetime | None = None,
) -> bool:
    """True if any change-controlled cap has been reached.

    Elapsed wall-clock time is derived from ``opened_at``/``now`` rather
    than a counter, so a CFI that has been open across restarts or
    scheduler gaps is judged the same way a live one would be.
    """
    stamp = now or _utcnow()
    elapsed_days = (stamp - opened_at).total_seconds() / 86400.0
    return (
        budget.planning_rounds_used >= spec.max_planning_rounds
        or elapsed_days >= spec.max_wall_clock_days
    )


def all_hypotheses_settled(investigation: CfiInvestigation) -> bool:
    """True once no hypothesis remains ``open`` (and at least one exists)."""
    if not investigation.hypotheses:
        return False
    return all(
        hypothesis.state is not CfiHypothesisState.OPEN
        for hypothesis in investigation.hypotheses
    )


def cfi_continue_gate(
    investigation: CfiInvestigation,
    *,
    spec: CfiBudgetSpec | None = None,
    now: datetime.datetime | None = None,
) -> tuple[bool, CfiInvestigation]:
    """Decides whether the CFI planning back-edge fires again.

    Returns:
        A ``(should_continue, investigation)`` pair. When the budget is
        exhausted, any still-``open`` hypothesis is force-settled to
        ``undetermined`` in the returned investigation before continuation
        is evaluated, so a budget-exhausted CFI never re-enters planning
        with an open hypothesis still on the ledger.
    """
    active_spec = spec or load_cfi_budget_spec()
    stamp = now or _utcnow()
    exhausted = budget_exhausted(
        investigation.budget,
        opened_at=investigation.opened_at,
        spec=active_spec,
        now=stamp,
    )
    working = investigation
    if exhausted:
        working = force_undetermined_on_budget_exhaustion(working, now=stamp)
        return False, working
    if all_hypotheses_settled(working):
        return False, working
    return True, working


def evaluate_cfi_closure(
    investigation: CfiInvestigation,
    *,
    spec: CfiBudgetSpec | None = None,
    now: datetime.datetime | None = None,
) -> CfiInvestigation:
    """Determines the terminal ``CfiOutcome`` and stamps ``closed_at``.

    Preconditions (enforced): every hypothesis must already be settled
    (``supported``/``refuted``/``undetermined``) — call ``cfi_continue_gate``
    until it returns ``should_continue = False`` before calling this.

    Outcome selection:
        - ``root_cause_identified`` when at least one hypothesis is
          ``supported`` with citations (a ``supported`` hypothesis always
          carries at least one supporting claim per the ledger's own
          settlement rule in ``cfi_ledger._settle_one``).
        - ``cfi_abandoned_budget_exhausted`` when the budget was exhausted
          and no hypothesis reached ``supported``.
        - ``root_cause_undetermined`` otherwise — every hypothesis was
          refuted or explicitly undetermined without exhausting the
          budget, the SOP's own legitimate "cause not apparent" outcome.
    """
    if investigation.outcome is not None and investigation.closed_at is not None:
        # Closure is idempotent. This node is rerun_on_resume, and the
        # post-closure Calendar/report nodes derive the site-review window
        # from closed_at while keying the Calendar event on cfi_id alone. A
        # second stamp would move the window under a stable event id, so a
        # retry would be rejected as "reused with a different start" and the
        # already-closed CFI would never get its artifacts.
        return investigation

    if any(
        hypothesis.state is CfiHypothesisState.OPEN
        for hypothesis in investigation.hypotheses
    ):
        raise ValueError(
            "Cannot close a CFI with an unsettled hypothesis still open."
        )

    active_spec = spec or load_cfi_budget_spec()
    stamp = now or _utcnow()
    exhausted = budget_exhausted(
        investigation.budget,
        opened_at=investigation.opened_at,
        spec=active_spec,
        now=stamp,
    )

    supported = [
        hypothesis
        for hypothesis in investigation.hypotheses
        if hypothesis.state is CfiHypothesisState.SUPPORTED
    ]
    if supported:
        outcome = CfiOutcome.ROOT_CAUSE_IDENTIFIED
    elif exhausted:
        outcome = CfiOutcome.ABANDONED_BUDGET_EXHAUSTED
    else:
        outcome = CfiOutcome.ROOT_CAUSE_UNDETERMINED

    return investigation.model_copy(
        update={"outcome": outcome, "closed_at": stamp}
    )
