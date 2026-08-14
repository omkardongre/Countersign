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

"""Case Memory graph nodes: retrieve prior art before Investigation, write
a fact after the QP has acted.

Neither node is an LLM and neither is on the citation gate's critical path:
a Case Memory outage degrades to an empty ``prior_art`` list (retrieval) or a
skipped write (after QP), not a failed case. Retrieval failures must not
block a live investigation over a store that is, by design, advisory input.
"""

from __future__ import annotations

import logging

from google.adk.workflow import node
from google.api_core.exceptions import GoogleAPICallError, RetryError
from mcp.shared.exceptions import McpError

from app.domain.case_memory_mcp import McpCaseMemoryStore
from app.domain.case_models import CaseComplianceResult
from app.domain.qp_models import QpConfirmationOutcome, QpReviewedPackage
from app.mcp.client import DepartmentToolCallError

# Failure modes an actual Case Memory outage (network, gRPC, MCP transport,
# tool-call error) can raise. Deliberately not bare Exception: a TypeError
# or KeyError from a real bug in this code path must fail loudly, not be
# treated the same as the store being unreachable.
_STORE_OUTAGE_ERRORS = (
    DepartmentToolCallError,
    McpError,
    GoogleAPICallError,
    RetryError,
    ConnectionError,
    TimeoutError,
)

logger = logging.getLogger(__name__)

_QUERY_TOP_K = 5


def _case(node_input: CaseComplianceResult | dict) -> CaseComplianceResult:
    if isinstance(node_input, CaseComplianceResult):
        return node_input
    return CaseComplianceResult.model_validate(node_input)


async def resolve_prior_art(case: CaseComplianceResult) -> CaseComplianceResult:
    """Attach tenant-filtered prior-deviation facts before Investigation runs.

    Query text is the product name plus classification rationale — enough
    for a "similar deviation on this product" match without the retriever
    having to parse full case findings. Kept separate from the ``@node``
    wrapper so it is unit-testable without ADK's node-execution machinery
    (same split as ``compliance.py``'s ``resolve_compliance_result``).
    """
    classified = case.classified
    query = f"{classified.product_name}: {classified.rationale}"
    try:
        result = await McpCaseMemoryStore().query_async(
            tenant_id=classified.tenant_id, query=query, top_k=_QUERY_TOP_K
        )
    except _STORE_OUTAGE_ERRORS as exc:
        # Advisory input only: a Case Memory outage does not stop the case.
        # A bug in this code path (TypeError, KeyError, ...) is not caught
        # here and must fail loudly instead of masquerading as an outage.
        logger.warning("Case Memory retrieval failed, continuing without it: %s", exc)
        return case
    return case.model_copy(
        update={
            "prior_art": result.facts,
            "prior_art_embedded": result.query_embedded,
        }
    )


@node(name="retrieve_prior_art")
async def retrieve_prior_art(node_input: CaseComplianceResult) -> CaseComplianceResult:
    return await resolve_prior_art(_case(node_input))


def _reviewed(node_input: QpReviewedPackage | dict) -> QpReviewedPackage:
    if isinstance(node_input, QpReviewedPackage):
        return node_input
    return QpReviewedPackage.model_validate(node_input)


def build_case_memory_fact(reviewed: QpReviewedPackage) -> str | None:
    """One durable sentence about this case for future prior-art queries.

    Only written once the QP has actually acted (confirmed, declined, or
    could not confirm) — a case still mid-investigation has nothing settled
    enough to become a fact for the *next* case to retrieve.
    """
    classified = reviewed.release_package.investigation.case.classified
    outcome = reviewed.qp_confirmation.outcome
    if outcome is QpConfirmationOutcome.CONFIRMED:
        return (
            f"Deviation {classified.case_id} on {classified.product_name} "
            f"(batch {classified.batch_id}, severity {classified.severity.value}) "
            "was confirmed by the QP after investigation."
        )
    if outcome is QpConfirmationOutcome.REJECTED:
        return None  # A refused confirm-while-not_ready is not a settled fact.
    return (
        f"Deviation {classified.case_id} on {classified.product_name} "
        f"(batch {classified.batch_id}, severity {classified.severity.value}) "
        f"was {outcome.value} by the QP: {reviewed.qp_confirmation.rationale}"
    )


def _qp_outcome_fact_id(tenant_id: str, case_id: str) -> str:
    """Deterministic key for this case's one QP-outcome fact.

    This node is ``rerun_on_resume=True``, so a crash between ``confirm_qp``
    completing and this write landing, followed by an ADK resume, reruns the
    node with the same ``QpReviewedPackage``. Without a stable key that
    rerun would write a second fact with a fresh ``uuid4`` id — the same
    "idempotency key on every mutating call" property ``sterility_start``
    already has (returns the existing row rather than inserting twice).
    Tenant is part of the key, not just ``case_id``, so the fact document
    id itself never crosses a tenant boundary.
    """
    return f"qp_outcome:{tenant_id}:{case_id}"


async def record_case_memory_impl(reviewed: QpReviewedPackage) -> QpReviewedPackage:
    """Write one fact about this case's outcome into Case Memory, then pass
    the reviewed package through unchanged — this node observes, it does not
    alter the QP's record. Kept separate from the ``@node`` wrapper so it is
    unit-testable (including rerun-idempotency) without ADK's node-execution
    machinery, same split as ``resolve_prior_art``/``retrieve_prior_art``.
    """
    fact = build_case_memory_fact(reviewed)
    if fact is None:
        return reviewed
    classified = reviewed.release_package.investigation.case.classified
    try:
        await McpCaseMemoryStore().write_fact_async(
            tenant_id=classified.tenant_id,
            case_id=classified.case_id,
            batch_id=classified.batch_id,
            fact=fact,
            source_uri=f"case://{classified.tenant_id}/{classified.case_id}",
            confidence=1.0,
            fact_id=_qp_outcome_fact_id(classified.tenant_id, classified.case_id),
        )
    except _STORE_OUTAGE_ERRORS as exc:
        # Same fail-open posture as retrieval: a Case Memory outage must not
        # revoke a QP decision that has already been recorded. A bug in this
        # code path is not caught here and must fail loudly.
        logger.warning("Case Memory write failed, continuing: %s", exc)
    return reviewed


@node(name="record_case_memory", rerun_on_resume=True)
async def record_case_memory(node_input: QpReviewedPackage) -> QpReviewedPackage:
    return await record_case_memory_impl(_reviewed(node_input))
