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

"""Async CFI Chat client used by the Runtime and scheduler paths.

The caller gets typed records only. Google Chat credentials, tenant-space
configuration, reply classification, and Cloud SQL writes remain inside
the department MCP process.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.cfi_interview_models import CfiInterviewRecord
from app.domain.department_tools import CHAT_READ_REPLIES, CHAT_SEND_QUESTION
from app.mcp.client import call_cfi_interview_tool


def _unwrap(payload: dict) -> dict:
    if "result" in payload and "interview_id" not in payload:
        return payload["result"]
    return payload


class McpCfiInterview:
    """Async-only client; every method crosses the department MCP boundary."""

    async def send_question_async(
        self,
        *,
        tenant_id: str,
        interview_id: str,
        cfi_id: str,
        hypothesis_id: str,
        round_number: int,
        question_number: int,
        question_text: str,
        deadline_at: datetime,
        app_name: str,
        user_id: str,
        session_id: str,
        invocation_id: str,
        interrupt_id: str,
        is_nudge: bool = False,
    ) -> CfiInterviewRecord:
        payload = await call_cfi_interview_tool(
            CHAT_SEND_QUESTION,
            {
                "tenant_id": tenant_id,
                "case_tenant_id": tenant_id,
                "interview_id": interview_id,
                "cfi_id": cfi_id,
                "hypothesis_id": hypothesis_id,
                "round_number": round_number,
                "question_number": question_number,
                "question_text": question_text,
                "deadline_at": deadline_at.isoformat(),
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id,
                "invocation_id": invocation_id,
                "interrupt_id": interrupt_id,
                "is_nudge": is_nudge,
            },
        )
        return CfiInterviewRecord.model_validate(_unwrap(payload or {}))

    async def read_replies_async(
        self,
        *,
        tenant_id: str,
        interview_id: str,
        finalize: bool = False,
    ) -> CfiInterviewRecord:
        payload = await call_cfi_interview_tool(
            CHAT_READ_REPLIES,
            {
                "tenant_id": tenant_id,
                "case_tenant_id": tenant_id,
                "interview_id": interview_id,
                "finalize": finalize,
            },
        )
        return CfiInterviewRecord.model_validate(_unwrap(payload or {}))
