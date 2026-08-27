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

"""Unit tests for the deterministic intake_and_classify workflow node.

Runs the node inside a minimal single-node ``Workflow`` via
``InMemoryRunner`` so the assertions cover the same input/output schema
enforcement that the real Countersign case-intake graph relies on, not just
the plain Python function underneath.
"""

import json

import pytest
from google.adk.apps import App
from google.adk.runners import InMemoryRunner
from google.adk.workflow import START, Workflow
from google.genai import types

from app.domain.intake import intake_and_classify
from app.domain.models import ClassifiedCase, DocType, LifecycleState


def _build_runner() -> InMemoryRunner:
    workflow = Workflow(
        name="test_intake_workflow",
        edges=[(START, intake_and_classify)],
    )
    app = App(name="test_intake_app", root_agent=workflow)
    return InMemoryRunner(app=app, app_name="test_intake_app")


def _intake_payload(**overrides) -> dict:
    payload = {
        "tenant_id": "aurex",
        "batch_id": "AX-2291",
        "product_name": "Test Product",
        "doc_type": DocType.BATCH_RECORD.value,
        "description": "unit test intake",
        "reported_by": "test-operator",
        "evidence_refs": ["drive://file/123"],
        "sterility_hold_seconds": 14 * 24 * 60 * 60,
    }
    payload.update(overrides)
    return payload


async def _run_intake(runner: InMemoryRunner, payload: dict) -> list:
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id="test-user"
    )
    message = types.Content(
        role="user",
        parts=[types.Part(text=json.dumps(payload))],
    )
    return [
        event
        async for event in runner.run_async(
            user_id="test-user",
            session_id=session.id,
            new_message=message,
        )
    ]


@pytest.mark.asyncio
async def test_intake_node_produces_classified_case() -> None:
    runner = _build_runner()
    payload = _intake_payload(patient_safety_impact=True)

    events = await _run_intake(runner, payload)

    data_events = [e for e in events if e.output is not None]
    assert len(data_events) == 1
    output = data_events[0].output

    case = ClassifiedCase.model_validate(output)
    assert case.tenant_id == "aurex"
    assert case.batch_id == "AX-2291"
    assert case.severity == "critical"
    assert case.lifecycle_state == LifecycleState.CLASSIFIED
    assert case.decision_authority == "none"
    assert case.evidence_refs == ["drive://file/123"]


@pytest.mark.asyncio
async def test_intake_node_defaults_to_minor() -> None:
    runner = _build_runner()
    payload = _intake_payload()

    events = await _run_intake(runner, payload)

    data_events = [e for e in events if e.output is not None]
    assert len(data_events) == 1
    case = ClassifiedCase.model_validate(data_events[0].output)
    assert case.severity == "minor"


@pytest.mark.asyncio
async def test_intake_node_rejects_invalid_payload() -> None:
    """Missing required fields must fail schema validation, not silently pass."""
    runner = _build_runner()
    payload = {"tenant_id": "aurex"}  # missing required fields

    with pytest.raises(ValueError):
        await _run_intake(runner, payload)
