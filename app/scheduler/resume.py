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

"""Deliver a sterility tick to a parked case invocation.

ADK resumes a paused ``RequestInput`` when it receives a function response
named ``adk_request_input`` carrying the interrupt id, on the original
``invocation_id`` (see ADK ``docs/runtime/resume.md``). Two transports:

* the in-process ``Runner``, when the case workflow runs in this service
* Agent Runtime ``:streamQuery``, when the case workflow is deployed there
  (``AdkApp.async_stream_query`` forwards ``invocation_id`` to the runner)

The tick is only a signal. The incubation store stays authoritative, so
delivering it twice cannot advance a hold that is not complete.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from google.genai import types

from app.domain.sterility_models import SterilityResumeHandle

CASE_ENGINE_ENV = "COUNTERSIGN_CASE_ENGINE"
RESUME_USER_AGENT = "countersign-sterility-scheduler"
REQUEST_INPUT_FUNCTION = "adk_request_input"


class ResumeError(Exception):
    """A resume attempt failed.

    ``recoverable`` distinguishes a dead handle (session/invocation gone —
    retrying will never succeed, an operator needs to intervene) from a
    transient failure (network blip, 5xx — a later tick or manual replay
    might succeed). Without this split, both looked identical in the tick
    response and an operator could not tell which completed holds actually
    need attention.
    """

    def __init__(self, message: str, *, recoverable: bool) -> None:
        super().__init__(message)
        self.recoverable = recoverable


def build_tick_message(
    *, tenant_id: str, batch_id: str, interrupt_id: str, source: str
) -> types.Content:
    """The function response ADK matches to the paused ``RequestInput``."""
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=interrupt_id,
                    name=REQUEST_INPUT_FUNCTION,
                    response={
                        "tenant_id": tenant_id,
                        "batch_id": batch_id,
                        "source": source,
                    },
                )
            )
        ],
    )


class ResumeClient(Protocol):
    async def resume(
        self,
        handle: SterilityResumeHandle,
        message: types.Content,
    ) -> None: ...


class RunnerResumeClient:
    """Resume a case running inside this process."""

    def __init__(self, runner: Any) -> None:
        self._runner = runner

    async def resume(
        self, handle: SterilityResumeHandle, message: types.Content
    ) -> None:
        from google.adk.errors.session_not_found_error import (
            SessionNotFoundError,
        )

        try:
            async for _ in self._runner.run_async(
                user_id=handle.user_id,
                session_id=handle.session_id,
                invocation_id=handle.invocation_id,
                new_message=message,
            ):
                pass
        except SessionNotFoundError as exc:
            # The session this handle points at no longer exists. Retrying
            # will never succeed; this needs an operator, not another tick.
            raise ResumeError(str(exc), recoverable=False) from exc


class AgentRuntimeResumeClient:
    """Resume a case deployed on Agent Runtime via ``:streamQuery``."""

    def __init__(self, engine_resource: str) -> None:
        self._engine = engine_resource
        self._location = engine_resource.split("/locations/")[1].split("/")[0]

    def _endpoint(self) -> str:
        return (
            f"https://{self._location}-aiplatform.googleapis.com/v1beta1/"
            f"{self._engine}:streamQuery?alt=sse"
        )

    def _authorized_session(self) -> Any:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return google.auth.transport.requests.AuthorizedSession(credentials)

    async def resume(
        self, handle: SterilityResumeHandle, message: types.Content
    ) -> None:
        import asyncio

        body = {
            "class_method": "async_stream_query",
            "input": {
                "user_id": handle.user_id,
                "session_id": handle.session_id,
                "invocation_id": handle.invocation_id,
                "message": message.model_dump(mode="json", exclude_none=True),
            },
        }

        def _post() -> None:
            session = self._authorized_session()
            response = session.post(
                self._endpoint(),
                data=json.dumps(body),
                headers={"Content-Type": "application/json"},
                stream=True,
            )
            try:
                if response.status_code >= 400:
                    # 404/400 means the session or invocation this handle
                    # names is gone — an operator needs to intervene, not a
                    # retry. Anything else (5xx, network) may recover.
                    recoverable = response.status_code not in (400, 404)
                    raise ResumeError(
                        "Agent Runtime resume failed "
                        f"({response.status_code}): {response.text[:500]}",
                        recoverable=recoverable,
                    )
                # Drain so the invocation runs to its next checkpoint.
                for _ in response.iter_lines():
                    pass
            finally:
                response.close()

        await asyncio.to_thread(_post)


def case_engine_resource() -> str | None:
    value = os.environ.get(CASE_ENGINE_ENV, "").strip()
    return value or None


def build_resume_client(runner: Any | None) -> ResumeClient:
    """Prefer the deployed Runtime; fall back to the local runner.

    Fails closed rather than silently skipping resume when neither is
    available, since a hold that completes without a wake-up is a case that
    quietly stalls.
    """
    engine = case_engine_resource()
    if engine:
        return AgentRuntimeResumeClient(engine)
    if runner is not None:
        return RunnerResumeClient(runner)
    raise RuntimeError(
        f"No resume transport: set {CASE_ENGINE_ENV} to the deployed Agent "
        "Runtime resource, or run the tick in the service hosting the case."
    )
