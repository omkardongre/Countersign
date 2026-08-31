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

"""CFI Phase 2 human interview node.

The node is a deterministic pause/resume boundary around a single-turn
question drafter. Google Chat I/O and durable interview state stay behind
the department MCP. A resumed tick is only a scoped signal; the MCP reads
and classifies the authoritative Chat thread before this node can continue.
"""

from __future__ import annotations

import datetime
import hashlib
import json

from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import node

from app.domain.cfi_interview_agent import (
    build_interview_content,
    get_interview_agent,
)
from app.domain.cfi_interview_chat import interview_deadline_seconds
from app.domain.cfi_interview_mcp import McpCfiInterview
from app.domain.cfi_interview_models import (
    CfiInterviewQuestionDraft,
    CfiInterviewResult,
    CfiInterviewStatus,
    CfiInterviewTickRequest,
    CfiReplyClassification,
)
from app.domain.cfi_models import CfiRoundState
from app.domain.master_data import load_cfi_budget_spec

CFI_INTERVIEW_INTERRUPT_PREFIX = "cfi_interview"


def _interview_id(state: CfiRoundState) -> str:
    material = (
        f"{state.investigation.cfi_id}:"
        f"{state.active_hypothesis_id}:"
        f"{state.round_number}"
    )
    return "cfi-interview-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]


def _interrupt_id(interview_id: str) -> str:
    return f"{CFI_INTERVIEW_INTERRUPT_PREFIX}:{interview_id}"


def _tick(payload: CfiInterviewTickRequest | dict) -> CfiInterviewTickRequest:
    body: object = payload
    if isinstance(body, dict):
        if "result" in body and "interview_id" not in body:
            body = body["result"]
        if isinstance(body, dict) and "response" in body and "interview_id" not in body:
            body = body["response"]
    return (
        body
        if isinstance(body, CfiInterviewTickRequest)
        else CfiInterviewTickRequest.model_validate(body)
    )


def _state(node_input: CfiRoundState | dict) -> CfiRoundState:
    return (
        node_input
        if isinstance(node_input, CfiRoundState)
        else CfiRoundState.model_validate(node_input)
    )


def _question_from_record(record) -> str:
    if not record.questions:
        raise ValueError("CFI interview has no persisted question to resume.")
    return record.questions[-1].question_text


def _request_input(record, *, interrupt_id: str) -> RequestInput:
    question = _question_from_record(record)
    return RequestInput(
        interrupt_id=interrupt_id,
        message=(
            "CFI human interview is waiting for an allowed responder. "
            f"Interview {record.interview_id}; question: {question} "
            f"Deadline: {record.deadline_at.isoformat()}. "
            "Reply in this Chat thread with observed facts, or reply "
            "'decline'/'stop'."
        ),
        payload={
            "interview_id": record.interview_id,
            "tenant_id": record.tenant_id,
            "cfi_id": record.cfi_id,
            "hypothesis_id": record.hypothesis_id,
            "thread_name": record.thread_name,
            "deadline_at": record.deadline_at.isoformat(),
            "question": question,
            "decision_authority": "none",
        },
        response_schema=CfiInterviewTickRequest,
    )


def _result(record) -> CfiInterviewResult:
    accepted = [
        reply
        for reply in record.replies
        if reply.sender_allowed
        and reply.classification
        in {
            CfiReplyClassification.ANSWER,
            CfiReplyClassification.DECLINE,
            CfiReplyClassification.STOP,
        }
    ]
    reply = max(accepted, key=lambda item: item.received_at) if accepted else None
    if record.status is CfiInterviewStatus.INTERVIEW_UNANSWERED:
        return CfiInterviewResult(
            interview_id=record.interview_id,
            status=record.status,
            answered=False,
            summary=record.outcome_summary
            or "No accepted reply arrived before the interview deadline.",
            source_uri=(
                f"google-chat://{record.space_name}/{record.thread_name}"
                if record.thread_name
                else f"google-chat-interview://{record.interview_id}"
            ),
        )
    if reply is None:
        raise ValueError(
            "CFI interview resumed without an accepted reply or unanswered outcome."
        )
    return CfiInterviewResult(
        interview_id=record.interview_id,
        status=record.status,
        answered=reply.classification is CfiReplyClassification.ANSWER,
        summary=(
            f"Human reply classified as {reply.classification.value}: "
            f"{record.outcome_summary or reply.raw_text.strip()}"
        )[:5000],
        source_uri=(
            f"google-chat://{record.space_name}/{record.thread_name}/"
            f"messages/{reply.message_name.rsplit('/', 1)[-1]}"
        ),
    )


@node(name="run_interview", rerun_on_resume=True)
async def run_interview(ctx: Context, node_input: CfiRoundState):
    """Draft, send, and pause for one CFI human interview answer.

    On first entry, the question is sent with a deterministic idempotency key
    and the resulting Chat thread is durably bound to the paused invocation.
    On resume, only a scheduler tick with the same tenant/interview scope is
    accepted; the MCP then reads the exact thread and returns typed evidence.
    Every ``chat_send_question`` and ``chat_read_replies`` call consumes the
    current planning round's change-controlled tool-call budget, including
    repeated scheduler polls while the interview remains unanswered.
    """
    state = _state(node_input)
    if state.active_hypothesis_id is None:
        raise ValueError("CFI interview route requires an active hypothesis.")

    interview_id = _interview_id(state)
    interrupt_id = _interrupt_id(interview_id)
    resume_inputs = ctx.resume_inputs or {}
    resume_input = resume_inputs.get(interrupt_id)
    mcp = McpCfiInterview()
    context_state = getattr(ctx, "state", None)
    if context_state is None:
        context_state = {}
    counter_key = f"cfi_interview_tool_calls:{interview_id}"
    calls_made = int(context_state.get(counter_key, 0))
    max_calls = load_cfi_budget_spec().max_tool_calls_per_round
    baseline_calls = state.investigation.budget.tool_calls_in_current_round

    def consume_tool_call() -> None:
        nonlocal calls_made
        if baseline_calls + calls_made >= max_calls:
            raise RuntimeError(
                "CFI interview tool-call budget is exhausted for this round."
            )
        calls_made += 1
        context_state[counter_key] = calls_made

    def budget_exhausted_result() -> CfiInterviewResult:
        return CfiInterviewResult(
            interview_id=interview_id,
            status=CfiInterviewStatus.INTERVIEW_UNANSWERED,
            answered=False,
            summary=(
                "The CFI interview tool-call budget was exhausted before an "
                "accepted human answer was captured."
            ),
            source_uri=f"google-chat-interview://{interview_id}",
        )

    def output_state(result: CfiInterviewResult) -> CfiRoundState:
        context_state[counter_key] = 0
        budget = state.investigation.budget.model_copy(
            update={
                "tool_calls_in_current_round": baseline_calls + calls_made,
            }
        )
        return state.model_copy(
            update={
                "investigation": state.investigation.model_copy(
                    update={"budget": budget}
                ),
                "pending_interview_result": result,
            }
        )

    if not resume_input:
        try:
            consume_tool_call()
        except RuntimeError:
            yield Event(output=output_state(budget_exhausted_result()).model_dump(mode="json"))
            return
        raw = await ctx.run_node(
            get_interview_agent(),
            node_input=build_interview_content(
                state.investigation,
                hypothesis_id=state.active_hypothesis_id,
                rationale=state.rationale,
            ),
        )
        if raw is None:
            raise ValueError("Interview agent returned no question draft.")
        draft = CfiInterviewQuestionDraft.model_validate(
            raw if isinstance(raw, (dict, CfiInterviewQuestionDraft)) else json.loads(raw)
        )
        deadline = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            seconds=interview_deadline_seconds()
        )
        record = await mcp.send_question_async(
            tenant_id=state.investigation.tenant_id,
            interview_id=interview_id,
            cfi_id=state.investigation.cfi_id,
            hypothesis_id=state.active_hypothesis_id,
            round_number=state.round_number,
            question_number=1,
            question_text=draft.question,
            deadline_at=deadline,
            app_name=ctx.session.app_name,
            user_id=ctx.user_id,
            session_id=ctx.session.id,
            invocation_id=ctx.invocation_id,
            interrupt_id=interrupt_id,
        )
        yield _request_input(record, interrupt_id=interrupt_id)
        return

    tick = _tick(resume_input)
    if (
        tick.tenant_id != state.investigation.tenant_id
        or tick.interview_id != interview_id
    ):
        raise ValueError("CFI interview tick scope does not match the paused CFI.")

    try:
        consume_tool_call()
    except RuntimeError:
        yield Event(output=output_state(budget_exhausted_result()).model_dump(mode="json"))
        return
    record = await mcp.read_replies_async(
        tenant_id=state.investigation.tenant_id,
        interview_id=interview_id,
        finalize=False,
    )
    if record.status is CfiInterviewStatus.REPLY_CAPTURED:
        try:
            consume_tool_call()
        except RuntimeError:
            # The captured reply is already typed by the MCP. It can be
            # consumed without a second finalize read when the round cap has
            # been reached.
            pass
        else:
            record = await mcp.read_replies_async(
                tenant_id=state.investigation.tenant_id,
                interview_id=interview_id,
                finalize=True,
            )
    if record.status not in {
        CfiInterviewStatus.COMPLETED,
        CfiInterviewStatus.INTERVIEW_UNANSWERED,
        CfiInterviewStatus.REPLY_CAPTURED,
    }:
        if baseline_calls + calls_made >= max_calls:
            yield Event(
                output=output_state(budget_exhausted_result()).model_dump(mode="json")
            )
        else:
            # A scheduler tick is allowed to be duplicated or arrive before a
            # valid answer. Re-check and park at the same durable checkpoint.
            yield _request_input(record, interrupt_id=interrupt_id)
        return

    result = (
        budget_exhausted_result()
        if record.status is CfiInterviewStatus.INTERVIEW_UNANSWERED
        and not record.outcome_summary
        else _result(record)
    )
    yield Event(output=output_state(result).model_dump(mode="json"))
