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

"""``qa_qms_write_agent``: the 8th fleet member, the only agent that mutates
an external record.

Runs after the QP has acted (``record_case_memory``). The report title/body
are pre-built deterministic text (``app.domain.qms_report``) — the model
never drafts report content, it only decides to call the one tool bound to
it. Same discipline as every other department agent in ``branches.py``: the
tool is a case-locked closure with no arguments the model controls, so the
agent cannot file a report for a different tenant or case than the one this
node was invoked for, and the filed artifact is never trusted as evidence —
this node observes/records, it does not alter the QP's decision.
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

from app.domain.qms_report import build_report_body, build_report_title
from app.domain.qms_report_models import QmsReportWriteRequest
from app.domain.qp_models import QpReviewedPackage
from app.mcp.client import call_qms_write_tool

QA_QMS_WRITE_AGENT_NAME = "qa_qms_write_agent"
QA_QMS_WRITE_MODEL = "gemini-3.6-flash"

_QA_QMS_WRITE_INSTRUCTION = """\
You are the QA/QMS filing agent for one GMP deviation case.

Call `file_qms_report` exactly once. You have no other tool. The report \
title and body are already prepared for you — you do not draft, edit, or \
summarize the report content. After the call, confirm that the report was \
filed.
"""


class QaQmsWriteAgentSummary(BaseModel):
    """The agent's own account of what it filed. Not used as evidence —
    the returned ``QmsReportWriteResult`` (captured via the tool closure) is.
    """

    report_filed: bool = False
    notes: str = Field(default="")


def _make_qms_write_tool(request: QmsReportWriteRequest, captured: dict[str, Any]) -> Any:
    """Tenant/case-locked closure: the model supplies no arguments, so it
    cannot file a report for a different tenant or case than this node's
    own bound ``request``.
    """

    async def file_qms_report() -> dict[str, Any]:
        """File the investigation report and Annex 16 package as a Google Doc."""
        payload = await call_qms_write_tool(
            "qms_report_write",
            {
                "tenant_id": request.tenant_id,
                "case_id": request.case_id,
                "title": request.title,
                "body": request.body,
            },
        )
        captured["result"] = payload
        return {
            "document_id": payload.get("document_id"),
            "was_update": payload.get("was_update"),
        }

    return file_qms_report


def get_qa_qms_write_agent(qms_write_tool: Any) -> Agent:
    """Built per call, per case: the tool is a tenant/case-locked closure."""
    return Agent(
        name=QA_QMS_WRITE_AGENT_NAME,
        model=Gemini(
            model=QA_QMS_WRITE_MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        mode="single_turn",
        include_contents="none",
        instruction=_QA_QMS_WRITE_INSTRUCTION,
        tools=[FunctionTool(qms_write_tool)],
        output_schema=QaQmsWriteAgentSummary,
    )


@node(name="file_qms_report", rerun_on_resume=True)
async def file_qms_report(
    ctx: Context, node_input: QpReviewedPackage
) -> QpReviewedPackage:
    """Files the report, then passes the reviewed package through unchanged.

    ``rerun_on_resume=True`` matches ``record_case_memory``: a crash between
    the QP's decision landing and this node completing, followed by an ADK
    resume, reruns this node with the same ``QpReviewedPackage``.
    ``qms_report_write`` is idempotent by ``(tenant_id, case_id)``
    (overwrites the existing filed doc rather than duplicating it), so a
    rerun here is safe.
    """
    reviewed = (
        node_input
        if isinstance(node_input, QpReviewedPackage)
        else QpReviewedPackage.model_validate(node_input)
    )
    classified = reviewed.release_package.investigation.case.classified
    request = QmsReportWriteRequest(
        tenant_id=classified.tenant_id,
        case_id=classified.case_id,
        title=build_report_title(reviewed),
        body=build_report_body(reviewed),
    )
    captured: dict[str, Any] = {}
    qms_write_tool = _make_qms_write_tool(request, captured)
    content = types.Content(
        role="user",
        parts=[
            types.Part(
                text=(
                    f"File the investigation report for case "
                    f"{classified.case_id}, tenant {classified.tenant_id}."
                )
            )
        ],
    )
    await ctx.run_node(get_qa_qms_write_agent(qms_write_tool), node_input=content)
    return reviewed
