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

"""``trend_correlation_agent``: chooses which historical comparison is
relevant to the active hypothesis. Its one tool is ``cfi_trend_query``,
tenant/batch-locked.

Same discipline as ``cfi_scope.py``: the agent decides *which* of three
fixed comparisons to run and supplies no tenant scope itself — that is
bound into the closure from the CFI record. The tool's return is captured
outside the model and becomes the CFI's evidence; the agent's own prose
summary is never itself citeable.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.adk.tools import FunctionTool
from google.genai import types

from app.domain.cfi_models import CfiInvestigation
from app.domain.cfi_trend_mcp import McpCfiTrendQuery
from app.domain.master_data import load_cfi_budget_spec


class CfiTrendToolBudgetExceeded(RuntimeError):
    """The round's ``max_tool_calls_per_round`` cap was exceeded."""


TREND_CORRELATION_AGENT_NAME = "trend_correlation_agent"
TREND_CORRELATION_MODEL = "gemini-3.5-flash-lite"

_INSTRUCTION = """\
You investigate one hypothesis in a GMP Cross-Functional Investigation by \
pulling one relevant historical comparison for the batch under \
investigation.

You are not the Quality Unit. A value difference or a recurrence count is \
an association for a human investigator to weigh, never itself a \
determination of cause.

Rules:

1. Call `cfi_trend_query` exactly once, choosing `kind` as one of:
   - "em_parameter_comparison": compare an environmental-monitoring
     parameter against another named batch on this tenant. Requires
     `compare_batch_id` and `parameter`.
   - "lims_test_comparison": compare a LIMS test result against another
     named batch on this tenant. Requires `compare_batch_id` and
     `test_code`.
   - "prior_cfi_recurrence": this tenant's own count of other open or
     closed CFIs, as a recurrence signal. No other arguments needed.
2. Do not call the tool more than once.
3. Only choose "em_parameter_comparison" or "lims_test_comparison" when the \
   already-captured evidence names a specific other batch and a specific \
   parameter/test to compare. Otherwise choose "prior_cfi_recurrence".
4. Summarize what the tool returned factually. Do not claim the comparison \
   proves or disproves the hypothesis.
5. `decision_authority` must be exactly "none".

After calling the tool, provide a short factual summary of what was found.
"""


def make_trend_query_tool(
    investigation: CfiInvestigation, captured: dict[str, Any]
) -> Any:
    """Public so ``cfi_graph`` binds the same tool closure this module's own
    agent uses, rather than maintaining a second, divergent tool-function
    definition in the graph.
    """
    max_calls = load_cfi_budget_spec().max_tool_calls_per_round

    async def cfi_trend_query(
        kind: str,
        compare_batch_id: str | None = None,
        parameter: str | None = None,
        test_code: str | None = None,
    ) -> dict[str, Any]:
        """Pull one historical trend comparison for the batch under
        investigation.

        Args:
            kind: One of "em_parameter_comparison", "lims_test_comparison",
                or "prior_cfi_recurrence".
            compare_batch_id: The other batch on this tenant to compare
                against. Required for the two comparison kinds.
            parameter: Environmental-monitoring parameter name. Required for
                "em_parameter_comparison".
            test_code: LIMS test code. Required for "lims_test_comparison".
        """
        calls_made = captured.get("tool_calls", 0)
        if calls_made >= max_calls:
            raise CfiTrendToolBudgetExceeded(
                f"This round already made {calls_made} cfi_trend_query "
                f"call(s), at the change-controlled cap of {max_calls}."
            )
        captured["tool_calls"] = calls_made + 1
        result = await McpCfiTrendQuery().query_async(
            tenant_id=investigation.tenant_id,
            batch_id=investigation.batch_id,
            cfi_id=investigation.cfi_id,
            kind=kind,
            compare_batch_id=compare_batch_id,
            parameter=parameter,
            test_code=test_code,
        )
        captured["result"] = result
        return {
            "kind": result.kind.value,
            "compare_batch_id": result.compare_batch_id,
            "parameter_or_test_code": result.parameter_or_test_code,
            "subject_mean": result.subject_mean,
            "compare_mean": result.compare_mean,
            "unit": result.unit,
            "delta": result.delta,
            "prior_cfi_count": result.prior_cfi_count,
        }

    return cfi_trend_query


def get_trend_correlation_agent(trend_query_tool: Any) -> Agent:
    """Built per call: the tool closure is bound to one CFI's tenant/batch."""
    return Agent(
        name=TREND_CORRELATION_AGENT_NAME,
        model=Gemini(
            model=TREND_CORRELATION_MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        mode="single_turn",
        include_contents="none",
        instruction=_INSTRUCTION,
        tools=[FunctionTool(trend_query_tool)],
    )


def build_trend_correlation_content(
    investigation: CfiInvestigation, *, rationale: str
) -> types.Content:
    evidence_summary = [
        {
            "evidence_id": item.evidence_id,
            "source_tool": item.source_tool,
            "fact": item.fact,
        }
        for item in investigation.evidence
    ]
    return types.Content(
        role="user",
        parts=[
            types.Part(
                text=(
                    "Investigate this hypothesis via trend correlation.\n"
                    f"Rationale from the planning round: {rationale}\n"
                    f"Already-captured evidence: {evidence_summary}"
                )
            )
        ],
    )
