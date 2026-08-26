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

"""Challenger agent: attacks the investigation draft from a clean context.

A separate agent, with no session history from the investigator, so it cannot
rubber-stamp its own prior wording. It does not certify evidence sufficiency;
the deterministic gate does that.
"""

from __future__ import annotations

import json

from google.adk.agents import Agent
from google.adk.agents.context import Context
from google.adk.models import Gemini
from google.adk.workflow import node
from google.genai import types

from app.domain.investigation_brief import build_investigation_brief
from app.domain.investigation_models import (
    ChallengedInvestigation,
    ChallengerReport,
    InvestigationDraftResult,
)

CHALLENGER_AGENT_NAME = "investigation_challenger"
CHALLENGER_MODEL = "gemini-3.5-flash-lite"

_INSTRUCTION = """\
You challenge a draft GMP deviation investigation. You did not write it.

Rules:

1. Use only the JSON brief's allowed_source_uris and allowed_finding_ids plus
   the draft itself. Do not invent evidence.
2. Attack unsourced claims, missing alternative causes that the brief's
   findings already support, overconfident hypotheses, and insufficient
   evidence. If you find none, return an empty attacks list.
3. Do not certify the draft. Do not recommend release or rejection.
4. `decision_authority` must be exactly `none`.
5. One pass only. Do not rewrite the draft.

Return only the structured challenger report.
"""


def get_challenger_agent() -> Agent:
    return Agent(
        name=CHALLENGER_AGENT_NAME,
        model=Gemini(
            model=CHALLENGER_MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        mode="single_turn",
        include_contents="none",
        instruction=_INSTRUCTION,
        output_schema=ChallengerReport,
        tools=[],
    )


@node(name="challenge_investigation", rerun_on_resume=True)
async def challenge_investigation(
    ctx: Context, node_input: InvestigationDraftResult
) -> ChallengedInvestigation:
    envelope = (
        node_input
        if isinstance(node_input, InvestigationDraftResult)
        else InvestigationDraftResult.model_validate(node_input)
    )
    if envelope.skipped or envelope.draft is None:
        return ChallengedInvestigation(
            case=envelope.case,
            skipped=True,
            skip_reason=envelope.skip_reason,
            draft=envelope.draft,
            challenger=None,
            decision_authority="none",
        )

    brief = build_investigation_brief(envelope.case)
    content = types.Content(
        role="user",
        parts=[
            types.Part(
                text=(
                    "Challenge this investigation draft against the brief.\n"
                    f"BRIEF:\n{json.dumps(brief, indent=2)}\n\n"
                    "DRAFT:\n"
                    f"{envelope.draft.model_dump_json(indent=2)}"
                )
            )
        ],
    )
    raw = await ctx.run_node(get_challenger_agent(), node_input=content)
    if raw is None:
        return ChallengedInvestigation(
            case=envelope.case,
            skipped=True,
            skip_reason="Challenger returned no structured output.",
            draft=envelope.draft,
            challenger=None,
            decision_authority="none",
        )
    report = ChallengerReport.model_validate(
        raw if isinstance(raw, (dict, ChallengerReport)) else json.loads(raw)
    )
    return ChallengedInvestigation(
        case=envelope.case,
        skipped=False,
        draft=envelope.draft,
        challenger=report,
        decision_authority="none",
    )
