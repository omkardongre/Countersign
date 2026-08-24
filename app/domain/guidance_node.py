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

"""Controlled-guidance graph node: retrieve SOP + public-guidance citations
before Investigation runs, same slot as ``retrieve_prior_art``.

Not an LLM and not on the citation gate's critical path in the sense of
"transport failures stop the case": a RAG Engine outage degrades to an
empty ``controlled_guidance`` list, the same fail-open posture Case Memory
retrieval already has for its own transport failures. What is different,
and deliberate, is what happens *after* that: if the controlled-SOP corpus
is configured for this deployment but this retrieval call found zero
in-force citations, ``investigation_gate.py``'s mandatory-SOP-citation check
still fails the case — a transport outage and "no controlled procedure
actually covers this" are both real problems, and neither is masked by a
silent empty list here. Whether the corpus is configured at all is read
directly from ``app/domain/guidance_source.py::sop_corpus_configured`` by
the gate, not inferred from this node's outcome.

Reached through ``McpControlledGuidanceStore``, the same MCP boundary Case
Memory retrieval already crosses: an Agent Identity principal cannot itself
hold the Vertex AI RAG Engine credential this call needs.
"""

from __future__ import annotations

import logging

from google.adk.workflow import node
from google.api_core.exceptions import GoogleAPICallError, RetryError
from mcp.shared.exceptions import McpError

from app.domain.case_models import CaseComplianceResult
from app.domain.guidance_mcp import McpControlledGuidanceStore
from app.domain.guidance_models import GuidanceCorpus
from app.mcp.client import DepartmentToolCallError

# Same outage-vs-bug split as case_memory_node.py's _STORE_OUTAGE_ERRORS: a
# real RAG Engine/network/MCP-transport failure degrades gracefully, a
# TypeError/KeyError from a bug in this code path must fail loudly instead
# of being swallowed as if the corpus were merely unreachable.
_RETRIEVAL_OUTAGE_ERRORS = (
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


async def resolve_controlled_guidance(
    case: CaseComplianceResult,
) -> CaseComplianceResult:
    """Attach manifest-verified SOP + public-guidance citations before
    Investigation runs. Query text mirrors ``resolve_prior_art``'s own
    "product plus classification rationale" shape — enough for "which
    controlled procedure and which public guidance cover this kind of
    deviation" without needing the investigation draft to exist first.
    """
    classified = case.classified
    query = f"{classified.product_name}: {classified.rationale}"
    store = McpControlledGuidanceStore()
    citations = []
    for corpus in (GuidanceCorpus.CONTROLLED_SOP, GuidanceCorpus.PUBLIC_GUIDANCE):
        try:
            result = await store.query_async(
                tenant_id=classified.tenant_id,
                corpus=corpus,
                query=query,
                top_k=_QUERY_TOP_K,
            )
        except _RETRIEVAL_OUTAGE_ERRORS as exc:
            logger.warning(
                "Controlled-guidance retrieval failed for corpus %s, "
                "continuing without it: %s",
                corpus.value,
                exc,
            )
            continue
        citations.extend(result.citations)
    return case.model_copy(update={"controlled_guidance": citations})


@node(name="retrieve_controlled_guidance")
async def retrieve_controlled_guidance(
    node_input: CaseComplianceResult,
) -> CaseComplianceResult:
    return await resolve_controlled_guidance(_case(node_input))
