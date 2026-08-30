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

"""Two focused Phase 3 checks: closure outcome selection and fleet size."""

from __future__ import annotations

from app.domain.cfi_gate import evaluate_cfi_closure
from app.domain.cfi_models import (
    CfiBudgetState,
    CfiHypothesis,
    CfiHypothesisState,
    CfiInvestigation,
    CfiOutcome,
    CfiTriggerReason,
)
from app.domain.fleet_manifest import build_fleet_manifest
from app.domain.master_data import RootCauseCategory


def _investigation(*, hypothesis_state: CfiHypothesisState) -> CfiInvestigation:
    return CfiInvestigation(
        tenant_id="aurex",
        source_case_id="case-1",
        batch_id="AX-2291",
        trigger_reasons=[CfiTriggerReason.GATE_FAILED],
        trigger_spec_version="cfi-trigger-v1",
        trigger_spec_sha256="trigger-sha",
        budget=CfiBudgetState(
            budget_spec_version="cfi-budget-v1", budget_spec_sha256="budget-sha"
        ),
        hypotheses=[
            CfiHypothesis(
                hypothesis_id="h1",
                category=RootCauseCategory.MACHINE,
                statement="The mixer caused the deviation.",
                state=hypothesis_state,
                opened_in_round=1,
                supporting_claims=(
                    [{"claim": "evidence", "evidence_ids": ["e1"]}]
                    if hypothesis_state is CfiHypothesisState.SUPPORTED
                    else []
                ),
            )
        ],
    )


def test_closure_outcome_depends_on_settled_hypothesis_state() -> None:
    supported = evaluate_cfi_closure(
        _investigation(hypothesis_state=CfiHypothesisState.SUPPORTED)
    )
    assert supported.outcome is CfiOutcome.ROOT_CAUSE_IDENTIFIED
    assert supported.closed_at is not None

    undetermined = evaluate_cfi_closure(
        _investigation(hypothesis_state=CfiHypothesisState.UNDETERMINED)
    )
    assert undetermined.outcome is CfiOutcome.ROOT_CAUSE_UNDETERMINED


def test_closure_is_idempotent_so_retried_artifacts_stay_stable() -> None:
    """A second closure must not move closed_at.

    The Calendar event id is derived from cfi_id while its start/end come from
    closed_at, so re-stamping would make a retry irreconcilable with the event
    already filed for the same CFI.
    """
    first = evaluate_cfi_closure(
        _investigation(hypothesis_state=CfiHypothesisState.SUPPORTED)
    )
    second = evaluate_cfi_closure(first)
    assert second.closed_at == first.closed_at
    assert second.outcome is first.outcome


def test_fleet_manifest_has_fifteen_agents_and_no_reasoning_authority() -> None:
    fleet = build_fleet_manifest()
    assert len(fleet) == 15
    assert all(entry.reasoning_authority is False for entry in fleet)
