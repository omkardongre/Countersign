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
    EM_AGENT_NAME,
    MANUFACTURING_AGENT_NAME,
    QC_AGENT_NAME,
    SUPPLY_AGENT_NAME,
)
from app.domain.case_memory_store import get_case_memory_store
from app.domain.challenger import CHALLENGER_AGENT_NAME
from app.domain.department_source import (
    coa_backend_name,
    em_backend_name,
    lims_backend_name,
)
from app.domain.investigation import INVESTIGATION_AGENT_NAME
from app.domain.qms_report_agent import QA_QMS_WRITE_AGENT_NAME, QA_QMS_WRITE_MODEL
from app.domain.workflow_meta import CASE_WORKFLOW_NAME
from app.sponsor_liaison.agent import SPONSOR_LIAISON_AGENT_NAME, SPONSOR_LIAISON_MODEL


def _case_memory_backend_name() -> str:
    """Reports the Case Memory backend the Investigation agent's
    ``prior_art_query`` tool actually reaches right now — Firestore KNN or
    real BigQuery ``VECTOR_SEARCH`` — the same "report what the code is
    actually doing" discipline as ``lims_backend_name``/``em_backend_name``
    in ``app/domain/department_source.py``.
    """
    return getattr(get_case_memory_store(), "backend_name", "firestore_case_memory")


class AgentKind(StrEnum):
    """Matches the ADK primitive actually used, per
    ``final_reseach/10_gap_analysis_and_win_plan.md`` section 4.1/4.2.
    """

    WORKFLOW = "workflow"
    LLM_AGENT_SINGLE_TURN = "llm_agent_single_turn"
    LLM_AGENT_CHAT_A2A_PEER = "llm_agent_chat_a2a_peer"
    DETERMINISTIC_NODE = "deterministic_node"


class FleetAgentEntry(BaseModel):
    """One agent's identity, mode, allowed tools, and backing system.

    ``reasoning_authority`` is deliberately always ``False``: no entry in
    this manifest is a decision-maker. That is the manifest's own honesty
    line, not just narration for the desk.

    ``backing_system`` is resolved at call time from
    ``app/domain/department_source.py`` rather than written here as prose.
    A hardcoded connector name is an overclaim the moment a department is
    still reading the synthetic catalog, and this manifest is rendered on
    screen, so it reports what the code is actually doing instead.
    """

    name: str
    kind: AgentKind
    department: str
    model: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    backing_system: str | None = None
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
            backing_system="google_drive",
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
            allowed_tools=["lims_query"],
            backing_system=lims_backend_name(),
            reasoning_authority=False,
            description=(
                "Real single-turn Agent bound to its own LIMS tool. "
                "Decides to call it; the deterministic check_lims runs "
                "only on the tool's captured typed snapshot, never on the "
                "agent's own text. Holds no database credential itself: "
                "the tool boundary is the credential boundary."
            ),
        ),
        FleetAgentEntry(
            name=EM_AGENT_NAME,
            kind=AgentKind.LLM_AGENT_SINGLE_TURN,
            department="environmental_monitoring",
            model="gemini-3.6-flash",
            allowed_tools=["em_query"],
            backing_system=em_backend_name(),
            reasoning_authority=False,
            description=(
                "Real single-turn Agent bound to its own environmental-"
                "monitoring tool, a separate department on a separate "
                "system from QC lab. Decides to call it; the deterministic "
                "check_em runs only on the tool's captured typed snapshot, "
                "never on the agent's own text. Holds no QC lab credential, "
                "and the QC lab agent holds none of its."
            ),
        ),
        FleetAgentEntry(
            name=SUPPLY_AGENT_NAME,
            kind=AgentKind.LLM_AGENT_SINGLE_TURN,
            department="supply",
            model="gemini-3.6-flash",
            allowed_tools=["coa_fetch"],
            backing_system=coa_backend_name(),
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
            allowed_tools=["prior_art_query", "controlled_guidance_query"],
            backing_system=_case_memory_backend_name(),
            reasoning_authority=False,
            description=(
                "Real single-turn Agent that may re-query prior deviations "
                "and controlled SOP/public guidance (Doc 12 T1-5) on this "
                "tenant, each at most once. Its draft is a proposal only: "
                "citations are checked afterwards by the deterministic "
                "investigation_gate, which also requires at least one "
                "in-force controlled-SOP citation whenever a corpus is "
                "configured. Any fact or citation it surfaces becomes "
                "citeable only by being captured from the real tool call, "
                "never by the agent's own assertion."
            ),
        ),
        FleetAgentEntry(
            name=SPONSOR_LIAISON_AGENT_NAME,
            kind=AgentKind.LLM_AGENT_CHAT_A2A_PEER,
            department="sponsor_liaison",
            model=SPONSOR_LIAISON_MODEL,
            allowed_tools=["sponsor_batch_status"],
            backing_system="cloud_sql_case_status",
            reasoning_authority=False,
            description=(
                "Agent 8 of 8: a real A2A peer running as its own, separate "
                "Cloud Run service (app/sponsor_liaison/), not a node in "
                "this graph. Answers a sponsor company's own QA agent one "
                "scoped question about one of their own batches, "
                "authenticated by a per-sponsor API key issued at Quality "
                "Agreement onboarding. Its one tool is locked to the "
                "already-authenticated sponsor identity, never to a "
                "model-supplied scope argument; every other tenant's data "
                "is denied at that boundary, not merely hidden from the "
                "prompt."
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
        FleetAgentEntry(
            name=QA_QMS_WRITE_AGENT_NAME,
            kind=AgentKind.LLM_AGENT_SINGLE_TURN,
            department="qa_qms",
            model=QA_QMS_WRITE_MODEL,
            allowed_tools=["qms_report_write"],
            backing_system="google_drive_docs",
            reasoning_authority=False,
            description=(
                "The only agent in this fleet permitted to mutate an "
                "external record, and only after the QP has acted "
                "(runs as file_qms_report, the graph's last node). Its "
                "one tool is a tenant/case-locked closure with no "
                "model-controlled arguments: it files the already-built "
                "investigation report and Annex 16 package as a Google "
                "Doc, it never drafts report content and never alters "
                "the QP's decision."
            ),
        ),
    ]
