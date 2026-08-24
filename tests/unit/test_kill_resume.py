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

"""Kill the runner, keep DatabaseSessionService rows, resume by invocation_id."""

from __future__ import annotations

import json

import pytest
from google.adk.apps import App, ResumabilityConfig
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.adk.workflow import node
from google.genai import types

from app.domain import case_graph as case_graph_mod
from app.domain import challenger as challenger_mod
from app.domain import extraction
from app.domain import investigation as investigation_mod
from app.domain.case_graph import build_deviation_case_workflow
from app.domain.investigation_models import (
    ChallengerReport,
    CitedClaim,
    InvestigationDraft,
    ProductImpactDraft,
    RootCauseHypothesis,
)
from app.domain.master_data import RootCauseCategory
from app.domain.sterility_store import get_sterility_store
from app.domain.sterility_wait import STERILITY_INTERRUPT_PREFIX
from app.domain.workflow_meta import WORKFLOW_VERSION_STATE_KEY
from tests.unit.test_case_graph import _case_payload, _request_input, _resume

_APP_NAME = "test_kill_resume_app"
_USER_ID = "test-qa-user"


def _stub_investigator():
    @node(name="stub_investigator")
    def stub_investigator(node_input) -> InvestigationDraft:
        return InvestigationDraft(
            tenant_id="ignored",
            case_id="ignored",
            batch_id="AX-2291",
            hypotheses=[
                RootCauseHypothesis(
                    category=RootCauseCategory.METHOD,
                    statement="Blend-speed excursion on the BMR.",
                    supporting_claims=[
                        CitedClaim(
                            claim="Manufacturing ingested the BMR page.",
                            source_uris=["drive://synthetic/bmr/AX-2291/page-14"],
                        )
                    ],
                    confidence=0.62,
                )
            ],
            product_impact=ProductImpactDraft(
                statement="Patient-safety impact is not determined by this draft.",
                supporting_claims=[
                    CitedClaim(
                        claim="Sterility-assurance impact is on the classified case.",
                        source_uris=["drive://synthetic/bmr/AX-2291/page-14"],
                    )
                ],
            ),
            decision_authority="none",
        )

    return stub_investigator


def _stub_challenger():
    @node(name="stub_challenger")
    def stub_challenger(node_input) -> ChallengerReport:
        return ChallengerReport(attacks=[], decision_authority="none")

    return stub_challenger


@pytest.fixture
def stub_ground_truth(monkeypatch):
    def _install(extraction_result):
        @node(name="stub_page_extractor")
        def stub_page_extractor(node_input) -> object:
            return extraction_result

        monkeypatch.setattr(
            extraction, "get_page_extractor", lambda spec=None: stub_page_extractor
        )

    return _install


@pytest.fixture
def stub_investigation_agents(monkeypatch):
    monkeypatch.setattr(
        investigation_mod, "get_investigation_agent", lambda *_args: _stub_investigator()
    )
    monkeypatch.setattr(
        challenger_mod, "get_challenger_agent", lambda: _stub_challenger()
    )


def _db_runner(session_service: DatabaseSessionService) -> Runner:
    app = App(
        name=_APP_NAME,
        root_agent=build_deviation_case_workflow(),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    return Runner(
        app=app,
        app_name=_APP_NAME,
        session_service=session_service,
        artifact_service=InMemoryArtifactService(),
    )


@pytest.mark.asyncio
async def test_new_runner_resumes_without_replaying_completed_intake(
    stub_ground_truth,
    stub_investigation_agents,
    monkeypatch,
) -> None:
    from tests.unit.evidence_fixtures import make_page_extraction

    stub_ground_truth(make_page_extraction())
    calls = {"intake": 0}
    original = case_graph_mod.classify_intake

    def _counting(intake):
        calls["intake"] += 1
        return original(intake)

    monkeypatch.setattr(case_graph_mod, "classify_intake", _counting)

    session_service = DatabaseSessionService(db_url="sqlite+aiosqlite:///:memory:")
    runner = _db_runner(session_service)
    session = await session_service.create_session(
        app_name=_APP_NAME, user_id=_USER_ID
    )
    payload = _case_payload()
    events = [
        event
        async for event in runner.run_async(
            user_id=_USER_ID,
            session_id=session.id,
            new_message=types.Content(
                role="user",
                parts=[types.Part(text=json.dumps(payload))],
            ),
        )
    ]
    invocation_id, interrupt_id, _ = _request_input(
        events, interrupt_prefix=STERILITY_INTERRUPT_PREFIX
    )
    assert interrupt_id.startswith("sterility_incubation:")
    assert calls["intake"] == 1

    persisted = await session_service.get_session(
        app_name=_APP_NAME, user_id=_USER_ID, session_id=session.id
    )
    assert persisted is not None
    assert persisted.events
    assert persisted.state.get(WORKFLOW_VERSION_STATE_KEY) == "deviation_case.1"

    killed = _db_runner(session_service)
    tick = {
        "tenant_id": "aurex",
        "batch_id": "AX-2291",
        "source": "scheduler",
    }
    waiting = await _resume(
        killed, session.id, invocation_id, interrupt_id, tick
    )
    _, interrupt_again, _ = _request_input(
        waiting, interrupt_prefix=STERILITY_INTERRUPT_PREFIX
    )
    assert calls["intake"] == 1

    get_sterility_store().complete("aurex", "AX-2291")
    resumed = await _resume(
        killed, session.id, invocation_id, interrupt_again, tick
    )
    _, qp_interrupt, _ = _request_input(resumed)
    assert qp_interrupt.startswith("qp_confirmation:")
    assert calls["intake"] == 1
