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

"""``escalate_to_cfi``: the case graph's one new terminal node.

Deterministic only — no agent, no LLM call. Runs ``decide_cfi_required()``
over the already-computed ``InvestigationCaseResult`` and, if a CFI is
warranted, creates and persists the seed ``CfiInvestigation`` record. It
does **not** run the CFI inline: the CFI graph is deployed as its own Agent
Runtime (``ARCHITECTURE.md`` section 7) and is
resumed separately, by id, once escalation has happened here.

This node does not alter, gate, or delay anything the case graph already
does. It runs as a **parallel terminal branch off ``gate_investigation``**,
the same fan-out shape ``intake_and_bind_evidence`` already uses for the
four department collectors — ``gate_investigation`` feeds both
``await_sterility_incubation`` (the existing, unchanged sequential path to
``confirm_qp``/``file_qms_report``) and this node, and this node's own
output goes nowhere else. A CFI escalation can therefore never block,
delay, or alter release/QP behavior; the proven sequential path is
byte-identical to before this node existed.
"""

from __future__ import annotations

from google.adk.workflow import node
from pydantic import BaseModel, field_validator

from app.domain.cfi_models import CfiBudgetState, CfiInvestigation, CfiLaunchStatus
from app.domain.cfi_store_mcp import McpCfiStore
from app.domain.cfi_trigger import CfiTriggerDecision, decide_cfi_required
from app.domain.investigation_models import InvestigationCaseResult
from app.domain.master_data import load_cfi_budget_spec


class CfiEscalationResult(BaseModel):
    """Output of ``escalate_to_cfi``. Carries the case through unchanged.

    ``cfi_id`` is set only when a CFI was actually opened this run —
    idempotent by ``(tenant_id, source_case_id)`` via
    ``McpCfiStore``'s underlying store's atomic open operation, so a
    resume-triggered rerun of this node does not open a second CFI for the
    same source case.
    """

    investigation: InvestigationCaseResult
    cfi_trigger_decision: CfiTriggerDecision
    cfi_id: str | None = None
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI escalation results cannot claim authority.")
        return value


async def _open_cfi(
    result: InvestigationCaseResult, decision: CfiTriggerDecision
) -> CfiInvestigation:
    """Atomically opens or reuses the CFI for this source case.

    The case Runtime never opens Cloud SQL directly. ``McpCfiStore.open_async``
    reaches the department MCP, where the store uses a unique
    ``(tenant_id, source_case_id)`` key and an atomic insert-or-read. A
    resume-triggered rerun therefore returns the original CFI, even when two
    retries race, without creating a duplicate or bypassing the credential
    boundary.
    """
    classified = result.case.classified
    budget_spec = load_cfi_budget_spec()
    seed = CfiInvestigation(
        tenant_id=classified.tenant_id,
        source_case_id=classified.case_id,
        batch_id=classified.batch_id,
        trigger_reasons=decision.reasons,
        trigger_spec_version=decision.trigger_spec_version,
        trigger_spec_sha256=decision.trigger_spec_sha256,
        budget=CfiBudgetState(
            budget_spec_version=budget_spec.spec_version,
            budget_spec_sha256=budget_spec.spec_sha256,
        ),
        launch_status=CfiLaunchStatus.PENDING,
    )
    return await McpCfiStore().open_async(seed)


@node(name="escalate_to_cfi", rerun_on_resume=True)
async def escalate_to_cfi(node_input: InvestigationCaseResult) -> None:
    """Deterministic: decides whether a CFI is required, and if so, opens
    (or reuses) one. Never blocks or alters the case graph's own outcome.

    Returns ``None`` deliberately: this is the case graph's *second*
    terminal node (alongside ``file_qms_report``), and ADK's ``Workflow``
    permits at most one terminal node to actually produce output — the
    proven sequential path through ``file_qms_report`` must remain that
    one output. This node's own result (``CfiEscalationResult``) is
    therefore computed and persisted for its side effect only (writing the
    seed ``CfiInvestigation`` to the store); the case graph's own caller
    reads whether a CFI was opened from the store, or from
    ``escalate_to_cfi_for_result`` directly in a test, not from this node's
    return value.
    """
    await escalate_to_cfi_for_result(node_input)
    return None


async def escalate_to_cfi_for_result(
    node_input: InvestigationCaseResult,
) -> CfiEscalationResult:
    """The testable, return-value-bearing core of ``escalate_to_cfi``.

    Separated from the ``@node``-wrapped function so unit tests can assert
    on ``CfiEscalationResult`` directly without needing to inspect Cloud SQL
    or reconstruct it from ``Workflow`` internals.
    """
    result = (
        node_input
        if isinstance(node_input, InvestigationCaseResult)
        else InvestigationCaseResult.model_validate(node_input)
    )
    decision = decide_cfi_required(result)
    cfi_id: str | None = None
    if decision.required:
        cfi = await _open_cfi(result, decision)
        cfi_id = cfi.cfi_id
    return CfiEscalationResult(
        investigation=result,
        cfi_trigger_decision=decision,
        cfi_id=cfi_id,
        decision_authority="none",
    )
