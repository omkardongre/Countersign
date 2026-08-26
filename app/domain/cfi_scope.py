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

"""``scope_expansion_agent``: chooses which sibling-batch attribute to
compare on. Its one tool is ``sibling_batch_query``, tenant/batch-locked.

The agent decides which shared attribute is worth searching on (Phase 1:
only ``"material_lot"``); it never supplies the tenant scope itself — that
is bound into the closure from the CFI record, the same discipline every
other tool closure in this codebase already has
(``investigation._make_prior_art_tool``, ``qms_report_agent._make_qms_write_tool``).

The tool's return is captured outside the model (``captured`` dict) and
becomes the CFI's evidence — the agent's own prose summary of what it found
is never itself citeable; only the captured ``SiblingBatchQueryResult`` is.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.adk.tools import FunctionTool
from google.genai import types

from app.domain.cfi_models import CfiInvestigation
from app.domain.cfi_scope_mcp import McpSiblingBatchQuery
from app.domain.master_data import load_cfi_budget_spec


class CfiToolBudgetExceeded(RuntimeError):
    """The round's ``max_tool_calls_per_round`` cap was exceeded."""

SCOPE_EXPANSION_AGENT_NAME = "scope_expansion_agent"
SCOPE_EXPANSION_MODEL = "gemini-3.5-flash-lite"

_INSTRUCTION = """\
You investigate one hypothesis in a GMP Cross-Functional Investigation by \
looking for other batches that share a suspect attribute with the batch \
under investigation.

You are not the Quality Unit. A shared attribute across batches is an \
association for a human investigator to weigh, never itself a determination \
of cause.

Rules:

1. Call `sibling_batch_query` exactly once, with `shared_attribute` set to \
   the attribute most relevant to the hypothesis you were given (Phase 1 \
   supports only "material_lot" — the shared raw-material lot named in \
   GMPSOP QMS-130 section 4.3).
2. Do not call the tool more than once.
3. Summarize what the tool returned factually — batch ids and the shared \
   value found. Do not claim the shared attribute proves or disproves the \
   hypothesis.
4. `decision_authority` must be exactly "none".

After calling the tool, provide a short factual summary of what was found.
"""


def make_sibling_batch_tool(
    investigation: CfiInvestigation, captured: dict[str, Any]
) -> Any:
    """Public so ``cfi_graph.run_scope_expansion`` binds the same tool
    closure this module's own agent uses, rather than maintaining a second,
    divergent tool-function definition in the graph.
    """
    max_calls = load_cfi_budget_spec().max_tool_calls_per_round

    async def sibling_batch_query(shared_attribute: str) -> dict[str, Any]:
        """Search for other batches on this tenant sharing an attribute
        with the batch under investigation.

        Args:
            shared_attribute: The attribute to compare on. Phase 1 supports
                only "material_lot".
        """
        calls_made = captured.get("tool_calls", 0)
        if calls_made >= max_calls:
            raise CfiToolBudgetExceeded(
                f"This round already made {calls_made} sibling_batch_query "
                f"call(s), at the change-controlled cap of {max_calls}."
            )
        captured["tool_calls"] = calls_made + 1
        result = await McpSiblingBatchQuery().query_async(
            tenant_id=investigation.tenant_id,
            batch_id=investigation.batch_id,
            shared_attribute=shared_attribute,
        )
        captured["result"] = result
        return {
            "candidates": [
                {
                    "batch_id": candidate.batch_id,
                    "shared_attribute": candidate.shared_attribute,
                    "shared_value": candidate.shared_value,
                }
                for candidate in result.candidates
            ]
        }

    return sibling_batch_query


def get_scope_expansion_agent(sibling_batch_tool: Any) -> Agent:
    """Built per call: the tool closure is bound to one CFI's tenant/batch."""
    return Agent(
        name=SCOPE_EXPANSION_AGENT_NAME,
        model=Gemini(
            model=SCOPE_EXPANSION_MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        mode="single_turn",
        include_contents="none",
        instruction=_INSTRUCTION,
        tools=[FunctionTool(sibling_batch_tool)],
    )


def build_scope_expansion_content(rationale: str) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part(
                text=(
                    "Investigate this hypothesis via scope expansion.\n"
                    f"Rationale from the planning round: {rationale}"
                )
            )
        ],
    )
