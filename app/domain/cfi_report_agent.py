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

"""The write-only CFI report agent and post-closure graph node.

The CFI aggregate is already closed before this node runs. Deterministic code
builds the title and body, while the single-turn agent has exactly one
closure-bound tool: it can file that prepared document, but it cannot draft,
edit, or choose a tenant, CFI, folder, or authority-bearing record.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import Agent
from google.adk.agents.context import Context
from google.adk.models import Gemini
from google.adk.tools import FunctionTool
from google.adk.workflow import node
from google.genai import types
from pydantic import BaseModel, Field

from app.domain.cfi_models import CfiInvestigation
from app.domain.cfi_report import build_cfi_report_body, build_cfi_report_title
from app.domain.cfi_report_mcp import McpCfiReportWriter
from app.domain.cfi_store_mcp import McpCfiStore

CFI_REPORTER_AGENT_NAME = "cfi_reporter_agent"
CFI_REPORTER_MODEL = "gemini-3.5-flash-lite"

_INSTRUCTION = """\
You are the CFI report filing agent for one durably closed investigation.

Call `file_cfi_report` exactly once. You have no other tool. The title and
body are already prepared by deterministic code. Do not draft, edit,
summarize, or reinterpret them. This document is an investigation artifact,
not a batch disposition, release decision, QP confirmation, or certified
root-cause determination. After the call, confirm the filing result.
"""


class CfiReporterAgentSummary(BaseModel):
    """The agent's non-authoritative account; the captured tool result wins."""

    report_filed: bool = False
    notes: str = Field(default="")


def _make_report_write_tool(
    investigation: CfiInvestigation, captured: dict[str, Any]
) -> Any:
    """Bind the writer to the already-validated closed CFI."""

    async def file_cfi_report() -> dict[str, Any]:
        """File the deterministic CFI report through the artifact MCP boundary."""
        result = await McpCfiReportWriter().write_async(
            tenant_id=investigation.tenant_id,
            cfi_id=investigation.cfi_id,
            title=build_cfi_report_title(investigation),
            body=build_cfi_report_body(investigation),
        )
        captured["result"] = result
        return {
            "document_id": result.document_id,
            "was_update": result.was_update,
            "decision_authority": result.decision_authority,
        }

    return file_cfi_report


def get_cfi_reporter_agent(report_write_tool: Any) -> Agent:
    """Build the stateless, write-only reporter for one CFI invocation."""
    return Agent(
        name=CFI_REPORTER_AGENT_NAME,
        model=Gemini(
            model=CFI_REPORTER_MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        mode="single_turn",
        include_contents="none",
        instruction=_INSTRUCTION,
        tools=[FunctionTool(report_write_tool)],
        output_schema=CfiReporterAgentSummary,
    )


def _artifact_error(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {detail[:2000]}"


@node(name="file_cfi_report", rerun_on_resume=True)
async def file_cfi_report(
    ctx: Context, node_input: CfiInvestigation
) -> CfiInvestigation:
    """File the deterministic report and persist success or typed failure.

    The Docs/Drive adapter is idempotent by tenant and CFI id. A retry can
    therefore update the same document in place. If the external write is
    unavailable, the error is stored on the CFI aggregate and the graph
    still returns a closed investigation with a visible incomplete artifact.
    """
    investigation = (
        node_input
        if isinstance(node_input, CfiInvestigation)
        else CfiInvestigation.model_validate(node_input)
    )
    if investigation.outcome is None or investigation.closed_at is None:
        raise ValueError("CFI report filing requires a durably closed CFI.")

    captured: dict[str, Any] = {}
    report_write_tool = _make_report_write_tool(investigation, captured)
    content = types.Content(
        role="user",
        parts=[
            types.Part(
                text=(
                    "File the prepared CFI report for CFI "
                    f"{investigation.cfi_id}, tenant {investigation.tenant_id}."
                )
            )
        ],
    )
    try:
        await ctx.run_node(
            get_cfi_reporter_agent(report_write_tool), node_input=content
        )
        result = captured.get("result")
        if result is None:
            raise RuntimeError("cfi_reporter_agent returned without filing the report.")
        updated = investigation.model_copy(
            update={"report": result, "report_error": None}
        )
    except Exception as exc:
        updated = investigation.model_copy(
            update={"report_error": _artifact_error(exc)}
        )
    return await McpCfiStore().save_async(updated)
