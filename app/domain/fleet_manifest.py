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

"""Fleet manifest: the real agents in the deviation-case graph, and what
each one is actually allowed to touch.

Modelled on the reference ``SubAgentRegistry`` pattern
(``research/reference-repos/agentic/adk-samples/core/python/long-horizon-harness/
horizon/subagents/registry.py``), scoped down to what this graph needs: a
static, deterministic description of the agents that exist right now, not a
live process registry. Nothing here calls a model or a tool; it reports the
shape of ``app/domain/case_graph.py`` so the desk and the film have one place
to show "this is the fleet" instead of reading it out of source.

Every entry corresponds to a real ``google.adk.agents.Agent`` node in the
graph (``app/domain/branches.py``, ``app/domain/investigation.py``,
``app/domain/challenger.py``), not a department name on a shared tool
surface. ``allowed_tools`` is the actual tool allow-list bound to that
agent's own tools list, not the full department-tool catalogue in
``app/domain/department_tools.py``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.branches import (
    MANUFACTURING_AGENT_NAME,
    QC_AGENT_NAME,
    SUPPLY_AGENT_NAME,
)
from app.domain.challenger import CHALLENGER_AGENT_NAME
from app.domain.investigation import INVESTIGATION_AGENT_NAME
from app.domain.workflow_meta import CASE_WORKFLOW_NAME


class AgentKind(StrEnum):
    """Matches the ADK primitive actually used, per
    ``final_reseach/10_gap_analysis_and_win_plan.md`` section 4.1/4.2.
    """

    WORKFLOW = "workflow"
    LLM_AGENT_SINGLE_TURN = "llm_agent_single_turn"
    DETERMINISTIC_NODE = "deterministic_node"


class FleetAgentEntry(BaseModel):
    """One agent's identity, mode, and allowed tools.

    ``reasoning_authority`` is deliberately always ``False``: no entry in
    this manifest is a decision-maker. That is the manifest's own honesty
    line, not just narration for the desk.
    """

    name: str
    kind: AgentKind
    department: str
    model: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    reasoning_authority: bool = False
    description: str


def build_fleet_manifest() -> list[FleetAgentEntry]:
    """Static description of every real agent node in the graph.

    Ordered to match the graph's own fan-out/fan-in structure: orchestrator,
    then the four parallel department collectors, then the sequential
    investigation/challenger pair.
    """
    return [
        FleetAgentEntry(
            name=CASE_WORKFLOW_NAME,
            kind=AgentKind.WORKFLOW,
            department="orchestrator",
            model=None,
            allowed_tools=[],
            reasoning_authority=False,
            description=(
                "Root ADK Workflow graph. Owns fan-out/fan-in, the "
                "sterility hold, the Annex 16 gate, and QP confirmation. "
                "Never a decision-maker itself."
            ),
        ),
        FleetAgentEntry(
            name=MANUFACTURING_AGENT_NAME,
            kind=AgentKind.LLM_AGENT_SINGLE_TURN,
            department="manufacturing",
            model="gemini-3.6-flash",
            allowed_tools=["drive_fetch_evidence"],
            reasoning_authority=False,
            description=(
                "Real single-turn Agent bound to its own Drive fetch tool. "
                "Decides to call it; the fetched bytes are captured from "
                "the real tool call and handed unchanged to the "
                "deterministic ingest -> extract -> validate -> compliance "
                "chain. The agent does not read or judge the fetched page "
                "itself, that is the separate document-vision extractor "
                "downstream."
            ),
        ),
        FleetAgentEntry(
            name=QC_AGENT_NAME,
            kind=AgentKind.LLM_AGENT_SINGLE_TURN,
            department="qc",
            model="gemini-3.6-flash",
            allowed_tools=["lims_query", "em_query"],
            reasoning_authority=False,
            description=(
                "Real single-turn Agent bound to its own two tools. "
                "Decides to call both; deterministic checks "
                "(check_lims/check_em) run only on the tools' captured "
                "typed snapshots, never on the agent's own text."
            ),
        ),
        FleetAgentEntry(
            name=SUPPLY_AGENT_NAME,
            kind=AgentKind.LLM_AGENT_SINGLE_TURN,
            department="supply",
            model="gemini-3.6-flash",
            allowed_tools=["coa_fetch"],
            reasoning_authority=False,
            description=(
                "Real single-turn Agent over the untrusted supplier-CoA "
                "channel. Only certificate metadata (never certificate "
                "text) is returned to the model as tool output. "
                "check_coa runs on the tool's captured typed snapshot."
            ),
        ),
        FleetAgentEntry(
            name=INVESTIGATION_AGENT_NAME,
            kind=AgentKind.LLM_AGENT_SINGLE_TURN,
            department="investigation",
            model="gemini-3.6-flash",
            allowed_tools=["prior_art_query"],
            reasoning_authority=False,
            description=(
                "Real single-turn Agent that may re-query prior deviations "
                "on this tenant at most once. Its draft is a proposal only: "
                "citations are checked afterwards by the deterministic "
                "investigation_gate, and any prior-art fact it surfaces "
                "becomes citeable only by being captured from the real "
                "tool call, never by the agent's own assertion."
            ),
        ),
        FleetAgentEntry(
            name=CHALLENGER_AGENT_NAME,
            kind=AgentKind.LLM_AGENT_SINGLE_TURN,
            department="investigation",
            model="gemini-3.6-flash",
            allowed_tools=[],
            reasoning_authority=False,
            description=(
                "Real single-turn Agent with a clean context (no shared "
                "history with the investigator), so it cannot rubber-stamp "
                "its own prior wording. Attacks are carried for the QP; "
                "they do not themselves fail the deterministic gate."
            ),
        ),
    ]
