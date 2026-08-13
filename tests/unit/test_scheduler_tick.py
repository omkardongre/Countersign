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

"""The scheduled tick completes due holds, wakes the parked case, and is guarded."""

from __future__ import annotations

import datetime

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.domain.sterility_models import (
    SterilityIncubationStatus,
    SterilityResumeHandle,
)
from app.domain.sterility_store import get_sterility_store
from app.scheduler import auth as auth_mod
from app.scheduler import resume as resume_mod
from app.scheduler.routes import attach_scheduler_routes

TICK_PATH = "/scheduler/sterility-tick"
HANDLE = SterilityResumeHandle(
    app_name="app",
    user_id="qp-1",
    session_id="session-1",
    invocation_id="invocation-1",
    interrupt_id="sterility_incubation:case-1",
)


def _park(
    *,
    handle: bool = True,
    started_at: datetime.datetime | None = None,
) -> None:
    """Park an incubation. Default start is old enough that the hold is due."""
    store = get_sterility_store()
    store.start(
        tenant_id="aurex",
        batch_id="AX-2291",
        case_id="case-1",
        workflow_version="deviation_case.1",
        now=started_at or datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    if handle:
        store.bind_resume_handle("aurex", "AX-2291", HANDLE)


class _RecordingRunner:
    """Stands in for the ADK Runner; records the resume call it receives."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run_async(self, **kwargs):
        self.calls.append(kwargs)
        return
        yield  # pragma: no cover - makes this an async generator


def _client(
    runner: object | None, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setenv("COUNTERSIGN_SCHEDULER_AUTH_DISABLED", "1")
    app = FastAPI()
    app.state.runner = runner
    attach_scheduler_routes(app)
    return TestClient(app, raise_server_exceptions=False)


def test_tick_resumes_using_the_persisted_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _park()
    runner = _RecordingRunner()
    body = _client(runner, monkeypatch).post(TICK_PATH).json()

    assert body["completed"] == 1
    assert body["resumed"] == 1
    assert body["decision_authority"] == "none"

    call = runner.calls[0]
    assert call["user_id"] == "qp-1"
    assert call["session_id"] == "session-1"
    assert call["invocation_id"] == "invocation-1"
    response = call["new_message"].parts[0].function_response
    assert response.name == "adk_request_input"
    assert response.id == "sterility_incubation:case-1"
    assert response.response == {
        "tenant_id": "aurex",
        "batch_id": "AX-2291",
        "source": "cloud-scheduler",
    }

    record = get_sterility_store().get("aurex", "AX-2291")
    assert record is not None
    assert record.status is SterilityIncubationStatus.COMPLETE


def test_tick_does_not_touch_a_hold_that_is_not_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _park(started_at=datetime.datetime.now(datetime.UTC))
    runner = _RecordingRunner()
    body = _client(runner, monkeypatch).post(TICK_PATH).json()

    assert body["completed"] == 0
    assert runner.calls == []
    record = get_sterility_store().get("aurex", "AX-2291")
    assert record is not None
    assert record.status is SterilityIncubationStatus.PENDING


def test_second_tick_does_not_resume_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _park()
    runner = _RecordingRunner()
    client = _client(runner, monkeypatch)

    assert client.post(TICK_PATH).json()["resumed"] == 1
    assert client.post(TICK_PATH).json() == {
        "completed": 0,
        "resumed": 0,
        "records": [],
        "decision_authority": "none",
    }
    assert len(runner.calls) == 1


def test_completed_hold_without_a_handle_is_reported_not_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _park(handle=False)
    runner = _RecordingRunner()
    body = _client(runner, monkeypatch).post(TICK_PATH).json()

    assert body["completed"] == 1
    assert body["resumed"] == 0
    assert body["records"][0]["reason"] == "no_resume_handle"
    assert runner.calls == []


def test_resume_failure_leaves_the_hold_complete_and_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _park()
    body = _client(None, monkeypatch).post(TICK_PATH).json()

    assert body["completed"] == 1
    assert body["resumed"] == 0
    assert "No resume transport" in body["records"][0]["reason"]
    record = get_sterility_store().get("aurex", "AX-2291")
    assert record is not None
    assert record.status is SterilityIncubationStatus.COMPLETE


def test_unconfigured_auth_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COUNTERSIGN_SCHEDULER_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("COUNTERSIGN_SCHEDULER_AUDIENCE", raising=False)
    monkeypatch.delenv("COUNTERSIGN_SCHEDULER_SERVICE_ACCOUNTS", raising=False)
    app = FastAPI()
    attach_scheduler_routes(app)

    assert TestClient(app, raise_server_exceptions=False).post(
        TICK_PATH
    ).status_code == 500


def test_only_an_allow_listed_caller_may_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COUNTERSIGN_SCHEDULER_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("COUNTERSIGN_SCHEDULER_AUDIENCE", "https://tick.example")
    monkeypatch.setenv(
        "COUNTERSIGN_SCHEDULER_SERVICE_ACCOUNTS",
        "tick@countersign.iam.gserviceaccount.com",
    )
    app = FastAPI()
    app.state.runner = _RecordingRunner()
    attach_scheduler_routes(app)
    client = TestClient(app, raise_server_exceptions=False)

    assert client.post(TICK_PATH).status_code == 401

    monkeypatch.setattr(
        auth_mod,
        "_verify_oidc_token",
        lambda token, audience: {"email": "someone-else@example.com"},
    )
    assert client.post(
        TICK_PATH, headers={"Authorization": "Bearer t"}
    ).status_code == 403

    monkeypatch.setattr(
        auth_mod,
        "_verify_oidc_token",
        lambda token, audience: {
            "email": "Tick@Countersign.iam.gserviceaccount.com"
        },
    )
    assert client.post(
        TICK_PATH, headers={"Authorization": "Bearer t"}
    ).status_code == 200


def test_invalid_signature_is_a_401_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COUNTERSIGN_SCHEDULER_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("COUNTERSIGN_SCHEDULER_AUDIENCE", "https://tick.example")
    monkeypatch.setenv("COUNTERSIGN_SCHEDULER_SERVICE_ACCOUNTS", "a@b.com")
    monkeypatch.setattr(
        auth_mod,
        "_verify_oidc_token",
        lambda token, audience: (_ for _ in ()).throw(ValueError("bad sig")),
    )
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer t")],
            "method": "POST",
            "path": TICK_PATH,
        }
    )
    with pytest.raises(HTTPException) as excinfo:
        auth_mod.verify_scheduler_token(request)
    assert excinfo.value.status_code == 401


def test_runtime_transport_is_preferred_when_engine_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "COUNTERSIGN_CASE_ENGINE",
        "projects/p/locations/us-central1/reasoningEngines/123",
    )
    client = resume_mod.build_resume_client(_RecordingRunner())

    assert isinstance(client, resume_mod.AgentRuntimeResumeClient)
    assert client._endpoint() == (
        "https://us-central1-aiplatform.googleapis.com/v1beta1/"
        "projects/p/locations/us-central1/reasoningEngines/123:streamQuery?alt=sse"
    )
