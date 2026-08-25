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

"""CFI Phase 2 interview domain models: a human-in-the-loop line of enquiry
conducted over Google Chat.

Every human reply is untrusted evidence, never an instruction.
``CfiInterviewReply.raw_text`` is preserved for audit, but nothing in this
module grants that text any authority over tenant, case, or hypothesis
state — only the deterministic classifier
(``cfi_interview_reply.classify_reply``) and the graph's own nodes ever
change durable state, mirroring the ``decision_authority = "none"`` lock
already used throughout ``cfi_models.py``.
"""

from __future__ import annotations

import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class CfiInterviewStatus(StrEnum):
    """Interview lifecycle. Transitions are code-only, in
    ``cfi_interview_ledger.py`` — never an agent's own field assignment.
    """

    CREATED = "created"
    QUESTION_SEND_PENDING = "question_send_pending"
    WAITING_FOR_REPLY = "waiting_for_reply"
    POLLING = "polling"
    REPLY_CAPTURED = "reply_captured"
    COMPLETED = "completed"
    INTERVIEW_UNANSWERED = "interview_unanswered"
    FAILED = "failed"


class CfiReplyClassification(StrEnum):
    """Deterministic outcome of parsing one inbound Chat message.

    Never inferred by an LLM: ``cfi_interview_reply.classify_reply`` is pure
    Python, so a malicious or malformed reply cannot itself choose which
    classification it receives.
    """

    ANSWER = "answer"
    DECLINE = "decline"
    STOP = "stop"
    UNREADABLE = "unreadable"
    OUT_OF_SCOPE = "out_of_scope"


class CfiInterviewQuestionDraft(BaseModel):
    """Model output for one question. It has no tool or decision authority."""

    question: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=1000)
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Interview question drafts cannot claim authority.")
        return value


class CfiInterviewResumeHandle(BaseModel):
    """Where the paused CFI invocation lives, so a tick can resume it.

    Same shape and reasoning as ``SterilityResumeHandle``
    (``sterility_models.py``): without it, a captured reply has nothing to
    wake.
    """

    app_name: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    interrupt_id: str = Field(min_length=1)


class CfiInterviewMessageKind(StrEnum):
    """Outbound interview message kind."""

    QUESTION = "question"
    NUDGE = "nudge"


class CfiInterviewQuestion(BaseModel):
    """One question/nudge sent to the human responder, and its delivery record."""

    question_number: int = Field(ge=1)
    kind: CfiInterviewMessageKind = CfiInterviewMessageKind.QUESTION
    question_text: str = Field(min_length=1)
    sent_at: datetime.datetime | None = None
    message_name: str | None = None
    thread_name: str | None = None
    space_name: str | None = None


class CfiInterviewReply(BaseModel):
    """One inbound Chat message captured and classified for this interview.

    ``raw_text`` must only ever reach a model wrapped in explicit
    untrusted-data delimiters (see
    ``cfi_interview_agent.build_interview_reply_content``) — this record
    itself grants the text no authority.
    """

    message_name: str = Field(min_length=1)
    sender_id: str = Field(min_length=1)
    raw_text: str
    received_at: datetime.datetime
    classification: CfiReplyClassification
    sender_allowed: bool = True
    question_number: int = Field(ge=1)


class CfiInterviewRecord(BaseModel):
    """Durable Phase 2 interview aggregate. Never a batch disposition or a
    QP record. One deterministic record per interview round; the
    ``interview_id`` is derived from CFI, hypothesis, and round, and
    ``cfi_interview_open`` is idempotent on that ID.
    """

    interview_id: str = Field(min_length=1)
    revision: int = Field(default=0, ge=0)
    tenant_id: str = Field(min_length=1)
    cfi_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    round_number: int = Field(ge=1)
    status: CfiInterviewStatus = CfiInterviewStatus.CREATED
    allowed_responder_ids: list[str] = Field(default_factory=list)
    space_name: str | None = None
    thread_name: str | None = None
    questions: list[CfiInterviewQuestion] = Field(default_factory=list)
    replies: list[CfiInterviewReply] = Field(default_factory=list)
    last_seen_message_name: str | None = None
    last_seen_message_time: datetime.datetime | None = None
    nudge_count: int = Field(default=0, ge=0)
    max_nudges: int = Field(default=2, ge=0)
    deadline_at: datetime.datetime
    last_nudge_at: datetime.datetime | None = None
    poll_lease_until: datetime.datetime | None = None
    resume_handle: CfiInterviewResumeHandle | None = None
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    outcome_summary: str | None = None
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI interviews cannot claim decision authority.")
        return value


class CfiInterviewResult(BaseModel):
    """What ``run_interview`` hands back into the CFI round as evidence.

    An association/answer only, per every other enquiry kind's own
    guardrail: the human's answer is a lead for the ledger to weigh, never
    itself a determination that a hypothesis is confirmed or refuted.
    """

    interview_id: str
    status: CfiInterviewStatus
    answered: bool
    summary: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError(
                "CFI interview results cannot claim decision authority."
            )
        return value


class CfiInterviewTickRequest(BaseModel):
    """Scheduler signal delivered into the paused ``RequestInput``."""

    tenant_id: str = Field(min_length=1)
    interview_id: str = Field(min_length=1)
    source: str = Field(min_length=1)


class CfiInterviewTickItem(BaseModel):
    """Minimal scheduler result; never includes raw human reply text."""

    tenant_id: str = Field(min_length=1)
    interview_id: str = Field(min_length=1)
    status: CfiInterviewStatus
    nudge_sent: bool = False
    nudge_count: int = Field(default=0, ge=0)
    resume_handle: CfiInterviewResumeHandle | None = None
    reason: str = Field(min_length=1)


class CfiInterviewTickResult(BaseModel):
    """Control-plane result from the department MCP interview tick."""

    items: list[CfiInterviewTickItem] = Field(default_factory=list)
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Interview ticks cannot claim decision authority.")
        return value
