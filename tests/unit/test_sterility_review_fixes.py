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

"""Fixes from the semantic review of the step-7 live-close slice.

Covers: cross-tenant sterility access, double-delivery on overlapping ticks,
stale-vs-transient resume failures, and the deploy script's session location.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.adk.errors.session_not_found_error import SessionNotFoundError

from app.domain.department_tools import (
    SterilityTenantMismatch,
    sterility_bind_resume_handle,
    sterility_get,
    sterility_start,
)
from app.domain.sterility_models import SterilityResumeHandle
from app.domain.sterility_store import get_sterility_store
from app.scheduler.resume import (
    AgentRuntimeResumeClient,
    ResumeError,
    RunnerResumeClient,
)
from app.scheduler.routes import attach_scheduler_routes

TICK_PATH = "/scheduler/sterility-tick"


class TestCrossTenantScoping:
    """A caller must not read or arm another tenant's clock."""

    def test_sterility_start_rejects_mismatched_case_tenant(self) -> None:
        with pytest.raises(SterilityTenantMismatch):
            sterility_start(
                tenant_id="aurex",
                batch_id="AX-2291",
                case_id="case-1",
                workflow_version="v1",
                case_tenant_id="novara",
                hold_seconds=14 * 24 * 60 * 60,
            )

    def test_sterility_get_rejects_mismatched_case_tenant(self) -> None:
        get_sterility_store().start(
            tenant_id="aurex",
            batch_id="AX-2291",
            case_id="case-1",
            workflow_version="v1",
            hold_seconds=14 * 24 * 60 * 60,
        )
        with pytest.raises(SterilityTenantMismatch):
            sterility_get(
                tenant_id="aurex", batch_id="AX-2291", case_tenant_id="novara"
            )

    def test_sterility_bind_resume_handle_rejects_mismatched_case_tenant(
        self,
    ) -> None:
        get_sterility_store().start(
            tenant_id="aurex",
            batch_id="AX-2291",
            case_id="case-1",
            workflow_version="v1",
            hold_seconds=14 * 24 * 60 * 60,
        )
        handle = SterilityResumeHandle(
            app_name="app",
            user_id="u",
            session_id="s",
            invocation_id="i",
            interrupt_id="int",
        )
        with pytest.raises(SterilityTenantMismatch):
            sterility_bind_resume_handle(
                tenant_id="aurex",
                batch_id="AX-2291",
                app_name=handle.app_name,
                user_id=handle.user_id,
                session_id=handle.session_id,
                invocation_id=handle.invocation_id,
                interrupt_id=handle.interrupt_id,
                case_tenant_id="novara",
            )

    def test_matching_case_tenant_is_allowed(self) -> None:
        record = sterility_start(
            tenant_id="aurex",
            batch_id="AX-2291",
            case_id="case-1",
            workflow_version="v1",
            case_tenant_id="aurex",
            hold_seconds=14 * 24 * 60 * 60,
        )
        assert record.tenant_id == "aurex"
        assert (
            sterility_get(
                tenant_id="aurex", batch_id="AX-2291", case_tenant_id="aurex"
            )
            is not None
        )


class TestAtomicTick:
    """Overlapping ticks must not deliver the same wake-up twice."""

    def test_tick_due_only_returns_a_row_once(self) -> None:
        store = get_sterility_store()
        due_at = store.start(
            tenant_id="aurex",
            batch_id="AX-2291",
            case_id="case-1",
            workflow_version="v1",
            hold_seconds=14 * 24 * 60 * 60,
            now=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        ).due_at

        first = store.tick_due(now=due_at)
        second = store.tick_due(now=due_at)

        assert len(first) == 1
        assert second == []

    def test_two_concurrent_callers_only_one_claims_the_row(self) -> None:
        """Simulates overlapping scheduler invocations against one store."""
        store = get_sterility_store()
        due_at = store.start(
            tenant_id="aurex",
            batch_id="AX-2291",
            case_id="case-1",
            workflow_version="v1",
            hold_seconds=14 * 24 * 60 * 60,
            now=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        ).due_at

        results = [store.tick_due(now=due_at) for _ in range(3)]
        claimed = [r for r in results if r]
        assert len(claimed) == 1
        assert len(claimed[0]) == 1

    def test_sqlite_store_tick_is_a_single_atomic_claim(self, tmp_path) -> None:
        from app.domain.sterility_store import SqliteSterilityStore

        store = SqliteSterilityStore(str(tmp_path / "sterility.sqlite"))
        due_at = store.start(
            tenant_id="aurex",
            batch_id="AX-2291",
            case_id="case-1",
            workflow_version="v1",
            hold_seconds=14 * 24 * 60 * 60,
            now=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        ).due_at

        results = [store.tick_due(now=due_at) for _ in range(3)]
        claimed = [r for r in results if r]
        assert len(claimed) == 1
        assert claimed[0][0].status.value == "complete"


class TestResumeErrorRecoverability:
    """A dead handle must be distinguishable from a transient failure."""

    @pytest.mark.asyncio
    async def test_session_not_found_is_reported_as_unrecoverable(self) -> None:
        class _RaisingRunner:
            async def run_async(self, **kwargs):
                raise SessionNotFoundError("gone")
                yield  # pragma: no cover

        handle = SterilityResumeHandle(
            app_name="app",
            user_id="u",
            session_id="s",
            invocation_id="i",
            interrupt_id="int",
        )
        client = RunnerResumeClient(_RaisingRunner())
        with pytest.raises(ResumeError) as excinfo:
            await client.resume(handle, message=object())
        assert excinfo.value.recoverable is False

    def test_agent_runtime_endpoint_shape(self) -> None:
        client = AgentRuntimeResumeClient(
            "projects/p/locations/us-central1/reasoningEngines/123"
        )
        assert client._endpoint().endswith(":streamQuery?alt=sse")

    def test_tick_reports_recoverable_flag_on_resume_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COUNTERSIGN_SCHEDULER_AUTH_DISABLED", "1")
        store = get_sterility_store()
        store.start(
            tenant_id="aurex",
            batch_id="AX-2291",
            case_id="case-1",
            workflow_version="v1",
            hold_seconds=14 * 24 * 60 * 60,
            now=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        )
        store.bind_resume_handle(
            "aurex",
            "AX-2291",
            SterilityResumeHandle(
                app_name="app",
                user_id="u",
                session_id="s",
                invocation_id="i",
                interrupt_id="int",
            ),
        )
        due_at = store.get("aurex", "AX-2291").due_at
        monkeypatch.setattr(
            type(store),
            "tick_due",
            lambda self, *, now=None: [store.complete("aurex", "AX-2291", now=due_at)],
        )

        class _FailingRunner:
            async def run_async(self, **kwargs):
                raise SessionNotFoundError("gone")
                yield  # pragma: no cover

        app = FastAPI()
        app.state.runner = _FailingRunner()
        attach_scheduler_routes(app)
        body = TestClient(app, raise_server_exceptions=False).post(
            TICK_PATH
        ).json()

        assert body["resumed"] == 0
        assert body["records"][0]["recoverable"] is False
