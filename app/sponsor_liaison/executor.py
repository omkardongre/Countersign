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

"""A2A ``AgentExecutor`` for the sponsor liaison.

Pattern verified against the official reference
(``research/reference-repos/agentic/a2a-samples/samples/python/agents/
adk_expense_reimbursement/agent_executor.py``): construct-or-reuse the
current ``Task``, run the ADK agent through a ``Runner``, and push status
updates onto the ``EventQueue``. This executor differs from that reference
in one deliberate way: the final reply is built from the tool-call capture
(``reply_from_capture``), not from the model's own text/JSON, matching this
codebase's rule that an agent's own assertion is never the answer.
"""

from __future__ import annotations

import uuid
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus
from a2a.utils.errors import UnsupportedOperationError
from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.sponsor_liaison.agent import (
    build_sponsor_liaison_agent,
    reply_from_capture,
)


class MissingSponsorIdentity(Exception):
    """``call_context.state`` had no authenticated sponsor_id.

    Would mean the auth dependency in ``app/sponsor_liaison/server.py`` did
    not run before this executor did — a wiring bug, not a caller error, so
    this is raised rather than treated as just another denial.
    """


class SponsorLiaisonAgentExecutor(AgentExecutor):
    """Runs the sponsor liaison agent for whichever sponsor authenticated
    this specific request.

    One instance is shared across all callers (unlike the reference
    sample's per-agent-instance executor), because the caller's identity is
    not known until the FastAPI auth dependency in ``server.py`` runs, which
    is *after* this executor is constructed but *before* ``execute`` is
    called. ``context.call_context.state["sponsor_id"]`` is where that
    dependency places it (via a custom ``ServerCallContextBuilder``,
    mirroring ``app/app_utils/a2a.py``'s ``_A2AServerCallContextBuilder``
    pattern already used for the case workflow's own A2A surface).

    A fresh ``Runner`` and fresh in-memory session/artifact services *per
    call* regardless: this agent's tool closure is locked to one
    ``sponsor_id`` per ``build_sponsor_liaison_agent`` call, and sharing a
    runner across concurrent requests from different sponsors would risk
    conflating one company's session state with another's for no benefit —
    this agent has no reason to remember anything between calls.
    """

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        sponsor_id = context.call_context.state.get("sponsor_id")
        if not sponsor_id:
            raise MissingSponsorIdentity(
                "No authenticated sponsor_id on this request's call context."
            )

        task = context.current_task
        if not task:
            task = Task(
                id=context.task_id or str(uuid.uuid4()),
                context_id=context.context_id or str(uuid.uuid4()),
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        query = context.get_user_input()
        captured: dict[str, Any] = {}
        agent = build_sponsor_liaison_agent(
            sponsor_id=sponsor_id, captured=captured
        )
        runner = Runner(
            app_name=agent.name,
            agent=agent,
            artifact_service=InMemoryArtifactService(),
            session_service=InMemorySessionService(),
        )
        session = await runner.session_service.create_session(
            app_name=agent.name, user_id=sponsor_id, state={}
        )
        content = types.Content(role="user", parts=[types.Part(text=query)])

        # tenant_id/batch_id for the fallback reply, best-effort from the
        # captured tool call only — never guessed from the query text.
        # reply_from_capture already handles "tool was never called"
        # without these; they only improve that reply's echoed fields.
        async for _event in runner.run_async(
            user_id=sponsor_id,
            session_id=session.id,
            new_message=content,
        ):
            pass  # single_turn: exactly one final event; no partial-progress
            # updates to stream for a status read this short.

        result = captured.get("result")
        reply = reply_from_capture(
            captured,
            fallback_tenant_id=result.tenant_id if result else "",
            fallback_batch_id=result.batch_id if result else "",
        )
        message = updater.new_agent_message(
            [Part(text=reply.model_dump_json())]
        )
        await updater.update_status(
            TaskState.TASK_STATE_COMPLETED, message=message
        )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise UnsupportedOperationError("Cancellation is not supported.")
