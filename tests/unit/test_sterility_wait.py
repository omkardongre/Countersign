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

"""Sterility incubation is external state; the workflow checkpoints until complete."""

from __future__ import annotations

import datetime

import pytest
from google.adk.workflow import node
from google.genai import types

from app.domain import challenger as challenger_mod
from app.domain import extraction
from app.domain import investigation as investigation_mod
from app.domain.investigation_models import (
    ChallengerReport,
    CitedClaim,
    InvestigationDraft,
    ProductImpactDraft,
    RootCauseHypothesis,
)
from app.domain.master_data import RootCauseCategory
from app.domain.sterility_models import SterilityIncubationStatus
from app.domain.sterility_store import (
    SqliteSterilityStore,
    get_sterility_store,
)
from app.domain.sterility_tick import tick_due_incubations
from app.domain.sterility_wait import (
    STERILITY_INTERRUPT_PREFIX,
    await_sterility_incubation,
)
from tests.unit.test_case_graph import (
    _case_payload,
    _request_input,
    _resume,
    _runner,
    _start,
)


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
            assert isinstance(node_input, types.Content)
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


def test_await_node_reruns_on_resume() -> None:
    assert await_sterility_incubation.rerun_on_resume is True
    assert await_sterility_incubation.name == "await_sterility_incubation"


def test_start_is_idempotent_and_defaults_to_fourteen_days() -> None:
    store = get_sterility_store()
    first = store.start(
        tenant_id="aurex",
        batch_id="AX-2291",
        case_id="case-1",
        workflow_version="deviation_case.1",
    )
    second = store.start(
        tenant_id="aurex",
        batch_id="AX-2291",
        case_id="case-2",
        workflow_version="deviation_case.1",
    )
    assert first.case_id == "case-1"
    assert second.started_at == first.started_at
    assert first.status is SterilityIncubationStatus.PENDING
    assert first.hold_seconds == 14 * 24 * 60 * 60
    assert first.compressed_for_demo is False
    assert first.decision_authority == "none"


def test_tick_is_idempotent_and_waits_until_due() -> None:
    store = get_sterility_store()
    started = store.start(
        tenant_id="aurex",
        batch_id="AX-2291",
        case_id="case-1",
        workflow_version="deviation_case.1",
        now=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    assert tick_due_incubations(
        now=datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)
    ) == []
    done = tick_due_incubations(now=started.due_at)
    assert len(done) == 1
    assert done[0].status is SterilityIncubationStatus.COMPLETE
    again = tick_due_incubations(now=started.due_at + datetime.timedelta(days=1))
    assert again == []


def test_sqlite_store_survives_a_new_connection(tmp_path) -> None:
    path = str(tmp_path / "sterility.sqlite")
    first = SqliteSterilityStore(path)
    first.start(
        tenant_id="aurex",
        batch_id="AX-2291",
        case_id="case-1",
        workflow_version="deviation_case.1",
    )
    first.complete("aurex", "AX-2291")
    second = SqliteSterilityStore(path)
    record = second.get("aurex", "AX-2291")
    assert record is not None
    assert record.status is SterilityIncubationStatus.COMPLETE


@pytest.mark.asyncio
async def test_graph_pauses_until_incubation_store_is_complete(
    stub_ground_truth,
    stub_investigation_agents,
) -> None:
    from tests.unit.evidence_fixtures import make_page_extraction

    stub_ground_truth(make_page_extraction())
    runner = _runner()
    session_id, events = await _start(runner, _case_payload())
    invocation_id, interrupt_id, args = _request_input(
        events, interrupt_prefix=STERILITY_INTERRUPT_PREFIX
    )
    assert invocation_id
    assert interrupt_id.startswith("sterility_incubation:")
    payload = args.get("payload") or {}
    assert payload.get("status") == SterilityIncubationStatus.PENDING.value
    assert payload.get("decision_authority") == "none"

    tick = {
        "tenant_id": "aurex",
        "batch_id": "AX-2291",
        "source": "scheduler",
    }
    still_waiting = await _resume(
        runner, session_id, invocation_id, interrupt_id, tick
    )
    _, interrupt_again, _ = _request_input(
        still_waiting, interrupt_prefix=STERILITY_INTERRUPT_PREFIX
    )
    assert interrupt_again.startswith("sterility_incubation:")

    get_sterility_store().complete("aurex", "AX-2291")
    resumed = await _resume(
        runner, session_id, invocation_id, interrupt_again, tick
    )
    _, qp_interrupt, qp_args = _request_input(resumed)
    assert qp_interrupt.startswith("qp_confirmation:")
    assert qp_args["payload"]["release_gate_status"] == "not_ready"


@pytest.mark.asyncio
async def test_scheduler_tick_wakes_a_case_parked_on_the_hold(
    stub_ground_truth,
    stub_investigation_agents,
    monkeypatch,
) -> None:
    """End to end: park on the hold, tick, and the same invocation continues."""
    import asyncio
    import types as pytypes

    from fastapi import Request

    from app.domain.sterility_wait import STERILITY_INTERRUPT_PREFIX
    from app.scheduler.routes import sterility_tick
    from tests.unit.evidence_fixtures import make_page_extraction

    # Compressed hold, labelled as such on the record.
    monkeypatch.setenv("COUNTERSIGN_STERILITY_HOLD_SECONDS", "1")
    stub_ground_truth(make_page_extraction())
    runner = _runner()
    session_id, events = await _start(runner, _case_payload())
    invocation_id, interrupt_id, _ = _request_input(
        events, interrupt_prefix=STERILITY_INTERRUPT_PREFIX
    )

    store = get_sterility_store()
    parked = store.get("aurex", "AX-2291")
    assert parked is not None
    assert parked.compressed_for_demo is True
    # The handle is what lets a tick find this invocation weeks later.
    assert parked.resume_handle is not None
    assert parked.resume_handle.invocation_id == invocation_id
    assert parked.resume_handle.session_id == session_id
    assert parked.resume_handle.interrupt_id == interrupt_id

    await asyncio.sleep(1.1)

    app = pytypes.SimpleNamespace(state=pytypes.SimpleNamespace(runner=runner))
    request = Request(
        {"type": "http", "headers": [], "method": "POST", "app": app}
    )
    result = await sterility_tick(request)

    assert result["completed"] == 1
    assert result["resumed"] == 1
    assert result["records"][0]["invocation_id"] == invocation_id

    session = await runner.session_service.get_session(
        app_name=runner.app_name,
        user_id=parked.resume_handle.user_id,
        session_id=session_id,
    )
    assert session is not None
    paused_at = [
        part.function_call.id
        for event in session.events
        for part in (event.content.parts or [] if event.content else [])
        if part.function_call and part.function_call.name == "adk_request_input"
    ]
    # The tick advanced the case past the hold to the QP signature.
    assert any(item.startswith("qp_confirmation:") for item in paused_at)


def test_compressed_hold_rejects_invalid_seconds(monkeypatch) -> None:
    from app.domain.sterility_store import incubation_hold

    monkeypatch.setenv("COUNTERSIGN_STERILITY_HOLD_SECONDS", "0")
    with pytest.raises(ValueError, match="must be > 0"):
        incubation_hold()
    monkeypatch.setenv("COUNTERSIGN_STERILITY_HOLD_SECONDS", "nope")
    with pytest.raises(ValueError, match="positive integer"):
        incubation_hold()
