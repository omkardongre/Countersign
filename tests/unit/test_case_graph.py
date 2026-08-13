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

"""End-to-end tests for the joined intake → evidence → compliance graph."""

import base64
import json

import pytest
from google.adk.apps import App
from google.adk.runners import InMemoryRunner
from google.adk.workflow import node
from google.genai import types

from app.domain import extraction
from app.domain.case_graph import CASE_WORKFLOW_NAME, build_deviation_case_workflow
from app.domain.case_models import CaseComplianceResult
from app.domain.compliance_models import ComplianceStatus
from app.domain.evidence_models import PageExtraction
from app.domain.extraction import get_page_extractor
from app.domain.models import DeviationSeverity, DocType, LifecycleState
from tests.unit.evidence_fixtures import SYNTHETIC_PAGE_BYTES, make_page_extraction

_APP_NAME = "test_case_app"
_USER_ID = "test-qa-user"


def _stub_extractor(extraction_result: PageExtraction):
    @node(name="stub_page_extractor")
    def stub_page_extractor(node_input) -> PageExtraction:
        assert isinstance(node_input, types.Content)
        assert any(
            part.inline_data is not None or part.file_data is not None
            for part in node_input.parts or []
        )
        return extraction_result

    return stub_page_extractor


@pytest.fixture
def stub_ground_truth(monkeypatch):
    def _install(extraction_result: PageExtraction) -> None:
        monkeypatch.setattr(
            extraction,
            "get_page_extractor",
            lambda spec=None: _stub_extractor(extraction_result),
        )

    return _install


def _runner() -> InMemoryRunner:
    app = App(name=_APP_NAME, root_agent=build_deviation_case_workflow())
    return InMemoryRunner(app=app, app_name=_APP_NAME)


def _case_payload(**intake_overrides) -> dict:
    intake = {
        "tenant_id": "aurex",
        "batch_id": "AX-2291",
        "product_name": "AXR450 Sterile Injectable",
        "doc_type": DocType.BATCH_RECORD.value,
        "description": "Blend-speed excursion flagged at in-process check.",
        "reported_by": "qa-shift-lead",
        "evidence_refs": ["drive://synthetic/bmr/AX-2291/page-14"],
        "sterility_assurance_impact": True,
    }
    intake.update(intake_overrides)
    return {
        "intake": intake,
        "evidence": {
            "source_uri": "drive://synthetic/bmr/AX-2291/page-14",
            "mime_type": "image/png",
            "content_base64": base64.b64encode(SYNTHETIC_PAGE_BYTES).decode(),
            "page_count": 1,
        },
    }


async def _run(runner: InMemoryRunner, payload: dict) -> list:
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id=_USER_ID
    )
    message = types.Content(
        role="user", parts=[types.Part(text=json.dumps(payload))]
    )
    return [
        event
        async for event in runner.run_async(
            user_id=_USER_ID, session_id=session.id, new_message=message
        )
    ]


def _final_result(events: list) -> CaseComplianceResult:
    outputs = [event.output for event in events if event.output is not None]
    assert outputs, "workflow produced no node output"
    return CaseComplianceResult.model_validate(outputs[-1])


def test_case_workflow_is_named_and_schema_bound() -> None:
    workflow = build_deviation_case_workflow()
    assert workflow.name == CASE_WORKFLOW_NAME
    assert workflow.input_schema is not None
    assert workflow.output_schema is not None


def test_platform_entrypoint_serves_the_case_workflow() -> None:
    from app.agent import root_agent

    assert root_agent.name == CASE_WORKFLOW_NAME


@pytest.mark.asyncio
async def test_classified_case_and_compliance_share_scope(
    stub_ground_truth,
) -> None:
    stub_ground_truth(make_page_extraction())
    result = _final_result(await _run(_runner(), _case_payload()))

    assert result.decision_authority == "none"
    assert result.classified.severity is DeviationSeverity.CRITICAL
    assert result.classified.lifecycle_state is LifecycleState.CLASSIFIED
    assert result.classified.tenant_id == result.compliance.tenant_id
    assert result.classified.case_id == result.compliance.case_id
    assert result.classified.batch_id == result.compliance.batch_id == "AX-2291"
    assert result.compliance.status is ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION
    assert result.compliance.decision_authority == "none"


@pytest.mark.asyncio
async def test_intake_schema_failure_stops_before_extraction(
    stub_ground_truth,
) -> None:
    stub_ground_truth(make_page_extraction())
    with pytest.raises(ValueError):
        await _run(_runner(), {"intake": {"tenant_id": "aurex"}, "evidence": {}})


@pytest.mark.asyncio
async def test_disallowed_mime_type_terminates_the_joined_graph(
    stub_ground_truth,
) -> None:
    stub_ground_truth(make_page_extraction())
    payload = _case_payload()
    payload["evidence"]["mime_type"] = "application/zip"
    with pytest.raises(Exception, match="not an accepted evidence type"):
        await _run(_runner(), payload)


def test_extractor_seam_is_unchanged() -> None:
    agent = get_page_extractor()
    assert agent.mode == "single_turn"
    assert agent.include_contents == "none"
