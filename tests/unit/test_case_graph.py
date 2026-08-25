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

"""End-to-end tests for the joined intake → departments → investigation graph."""

import base64
import json

import pytest
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import InMemoryRunner
from google.adk.workflow import node
from google.genai import types

from app.domain import branches as branches_mod
from app.domain import challenger as challenger_mod
from app.domain import department_source as department_source_mod
from app.domain import extraction
from app.domain import investigation as investigation_mod
from app.domain import qms_report_agent as qms_report_agent_mod
from app.domain.case_graph import CASE_WORKFLOW_NAME, build_deviation_case_workflow
from app.domain.challenger import get_challenger_agent
from app.domain.compliance_models import ComplianceStatus
from app.domain.department_catalog import lookup_department_snapshot
from app.domain.department_models import LimsResultRow, LimsSnapshot
from app.domain.evidence_models import PageExtraction
from app.domain.extraction import get_page_extractor
from app.domain.investigation import get_investigation_agent
from app.domain.investigation_models import (
    ChallengerReport,
    CitedClaim,
    InvestigationDraft,
    ProductImpactDraft,
    RootCauseHypothesis,
)
from app.domain.master_data import RootCauseCategory
from app.domain.models import DeviationSeverity, DocType, LifecycleState
from app.domain.qp_models import (
    QpConfirmationOutcome,
    QpReviewedPackage,
)
from app.domain.release_models import (
    CaseReleasePackage,
    ChecklistItemStatus,
    ReleaseGateStatus,
)
from tests.unit.evidence_fixtures import SYNTHETIC_PAGE_BYTES, make_page_extraction

_APP_NAME = "test_case_app"
_USER_ID = "test-qa-user"
_REQUEST_INPUT_NAME = "adk_request_input"


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


def _stub_investigator():
    @node(name="stub_investigator")
    def stub_investigator(node_input) -> InvestigationDraft:
        assert isinstance(node_input, types.Content)
        return InvestigationDraft(
            tenant_id="ignored",
            case_id="ignored",
            batch_id="AX-2291",
            hypotheses=[
                RootCauseHypothesis(
                    category=RootCauseCategory.METHOD,
                    statement=(
                        "Blend-speed excursion on the BMR is the leading "
                        "process deviation to investigate."
                    ),
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
        assert isinstance(node_input, types.Content)
        return ChallengerReport(attacks=[], decision_authority="none")

    return stub_challenger


@pytest.fixture
def stub_department_agents(monkeypatch):
    """Replaces the real Gemini calls in the QC/supply agents with a stub
    that still calls the real tool closures, so real MCP/catalog data flows
    through the same deterministic checks as production — only the LLM turn
    itself is skipped, matching the existing extractor/investigation stubs.
    """

    def _stub_get_qc_agent(lims_tool):
        @node(name="stub_qc_agent")
        async def stub_qc_agent(node_input):
            await lims_tool()
            return branches_mod.QcAgentSummary(lims_reviewed=True)

        return stub_qc_agent

    def _stub_get_em_agent(em_tool):
        @node(name="stub_em_agent")
        async def stub_em_agent(node_input):
            await em_tool()
            return branches_mod.EmAgentSummary(em_reviewed=True)

        return stub_em_agent

    def _stub_get_supply_agent(coa_tool, eudragmdp_tool, openfda_tool):
        @node(name="stub_supply_agent")
        async def stub_supply_agent(node_input):
            await coa_tool()
            await eudragmdp_tool()
            await openfda_tool()
            return branches_mod.SupplyAgentSummary(
                coa_reviewed=True,
                eudragmdp_screened=True,
                openfda_screened=True,
            )

        return stub_supply_agent

    monkeypatch.setattr(branches_mod, "get_qc_agent", _stub_get_qc_agent)
    monkeypatch.setattr(branches_mod, "get_em_agent", _stub_get_em_agent)
    monkeypatch.setattr(branches_mod, "get_supply_agent", _stub_get_supply_agent)


@pytest.fixture
def stub_ground_truth(monkeypatch):
    def _install(extraction_result: PageExtraction) -> None:
        monkeypatch.setattr(
            extraction,
            "get_page_extractor",
            lambda spec=None: _stub_extractor(extraction_result),
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


@pytest.fixture
def stub_qms_write_agent(monkeypatch):
    """Replaces the real Gemini call *and* the real Docs/Drive MCP call in
    ``file_qms_report`` with a stub. Unlike ``stub_department_agents``
    (which still exercises the real in-process MCP tool against synthetic
    catalog data), the Docs/Drive API is external and out of scope for
    these graph-flow tests — ``test_qms_report.py`` covers the deterministic
    report content, and the MCP tool itself is exercised separately. Any
    test that resumes the graph past ``confirm_qp`` reaches
    ``file_qms_report`` and needs this stub so it does not attempt a real
    network call.
    """

    async def _stub_call_qms_write_tool(name, arguments):
        del name
        return {
            "document_id": "stub-doc-id",
            "document_url": "https://docs.google.com/document/d/stub-doc-id/edit",
            "was_update": False,
        }

    monkeypatch.setattr(
        qms_report_agent_mod, "call_qms_write_tool", _stub_call_qms_write_tool
    )

    def _stub_get_qa_qms_write_agent(qms_write_tool):
        @node(name="stub_qa_qms_write_agent")
        async def stub_qa_qms_write_agent(node_input):
            await qms_write_tool()
            return qms_report_agent_mod.QaQmsWriteAgentSummary(report_filed=True)

        return stub_qa_qms_write_agent

    monkeypatch.setattr(
        qms_report_agent_mod, "get_qa_qms_write_agent", _stub_get_qa_qms_write_agent
    )


def _runner() -> InMemoryRunner:
    app = App(
        name=_APP_NAME,
        root_agent=build_deviation_case_workflow(),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
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


async def _start(runner: InMemoryRunner, payload: dict) -> tuple[str, list]:
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id=_USER_ID
    )
    message = types.Content(
        role="user", parts=[types.Part(text=json.dumps(payload))]
    )
    events = [
        event
        async for event in runner.run_async(
            user_id=_USER_ID, session_id=session.id, new_message=message
        )
    ]
    return session.id, events


def _gate_package(events: list) -> CaseReleasePackage:
    outputs = [event.output for event in events if event.output is not None]
    assert outputs, "workflow produced no node output"
    return CaseReleasePackage.model_validate(outputs[-1])


def _request_input(
    events: list, *, interrupt_prefix: str = "qp_confirmation"
) -> tuple[str, str, dict]:
    for event in events:
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            call = part.function_call
            if call is None or call.name != _REQUEST_INPUT_NAME:
                continue
            interrupt_id = str(call.id or "")
            if not interrupt_id.startswith(interrupt_prefix):
                continue
            return event.invocation_id, interrupt_id, dict(call.args or {})
    raise AssertionError(
        f"workflow did not pause for {interrupt_prefix} confirmation"
    )


async def _resume(
    runner: InMemoryRunner,
    session_id: str,
    invocation_id: str,
    interrupt_id: str,
    response: dict,
) -> list:
    message = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=interrupt_id,
                    name=_REQUEST_INPUT_NAME,
                    response=response,
                )
            )
        ],
    )
    return [
        event
        async for event in runner.run_async(
            user_id=_USER_ID,
            session_id=session_id,
            new_message=message,
            invocation_id=invocation_id,
        )
    ]


def _reviewed(events: list) -> QpReviewedPackage:
    outputs = [event.output for event in events if event.output is not None]
    assert outputs, "resume produced no node output"
    return QpReviewedPackage.model_validate(outputs[-1])


def _annex_item(package: CaseReleasePackage, annex_ref: str):
    return next(
        item for item in package.release_gate.items if item.annex_ref == annex_ref
    )


def test_case_workflow_is_named_and_schema_bound() -> None:
    workflow = build_deviation_case_workflow()
    assert workflow.name == CASE_WORKFLOW_NAME
    assert workflow.input_schema is not None
    assert workflow.output_schema is QpReviewedPackage


def test_platform_entrypoint_serves_the_case_workflow() -> None:
    from app.agent import root_agent

    assert root_agent.name == CASE_WORKFLOW_NAME


@pytest.mark.asyncio
async def test_classified_case_and_compliance_share_scope(
    stub_ground_truth,
    stub_investigation_agents,
    stub_department_agents,
    sterility_released,
) -> None:
    stub_ground_truth(make_page_extraction())
    runner = _runner()
    _, events = await _start(runner, _case_payload())
    result = _gate_package(events)
    invocation_id, interrupt_id, args = _request_input(events)
    assert invocation_id
    assert interrupt_id.startswith("qp_confirmation:")
    assert args["payload"]["release_gate_status"] == ReleaseGateStatus.NOT_READY
    investigation = result.investigation
    case = investigation.case

    assert result.decision_authority == "none"
    assert investigation.decision_authority == "none"
    assert investigation.gate.passed is True
    assert investigation.draft is not None
    assert investigation.challenger is not None
    assert investigation.draft.decision_authority == "none"
    assert result.release_gate.decision_authority == "none"
    assert result.release_gate.status is ReleaseGateStatus.NOT_READY
    assert _annex_item(result, "1.7.11").status is ChecklistItemStatus.PASS
    assert _annex_item(result, "1.7.13").status is ChecklistItemStatus.PASS
    assert _annex_item(result, "1.7.6").status is ChecklistItemStatus.PASS
    assert _annex_item(result, "1.7.16").status is ChecklistItemStatus.FAIL
    assert _annex_item(result, "1.7.17").status is ChecklistItemStatus.FAIL
    assert _annex_item(result, "1.7.18").status is ChecklistItemStatus.NOT_EVALUATED
    assert case.classified.severity is DeviationSeverity.CRITICAL
    assert case.classified.lifecycle_state is LifecycleState.CLASSIFIED
    assert case.compliance is not None
    assert case.classified.tenant_id == case.compliance.tenant_id
    assert case.classified.case_id == case.compliance.case_id
    assert case.classified.batch_id == case.compliance.batch_id == "AX-2291"
    assert case.aggregated_status is ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION
    assert case.compliance.status is ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION
    assert case.compliance.decision_authority == "none"
    departments = {branch.department.value for branch in case.branches}
    assert departments == {"manufacturing", "qc", "environmental_monitoring", "supply"}
    assert all(branch.status.value == "ok" for branch in case.branches)
    assert case.findings == []


@pytest.mark.asyncio
async def test_intake_schema_failure_stops_before_extraction(
    stub_ground_truth,
) -> None:
    stub_ground_truth(make_page_extraction())
    with pytest.raises(ValueError):
        await _start(_runner(), {"intake": {"tenant_id": "aurex"}, "evidence": {}})


@pytest.mark.asyncio
async def test_disallowed_mime_type_fails_manufacturing_without_stalling_join(
    stub_ground_truth,
    stub_investigation_agents,
    stub_department_agents,
    sterility_released,
) -> None:
    stub_ground_truth(make_page_extraction())
    payload = _case_payload()
    payload["evidence"]["mime_type"] = "application/zip"
    result = _gate_package((await _start(_runner(), payload))[1])
    case = result.investigation.case

    manufacturing = next(
        branch
        for branch in case.branches
        if branch.department.value == "manufacturing"
    )
    assert manufacturing.status.value == "failed"
    assert "not an accepted evidence type" in (manufacturing.error or "")
    assert case.aggregated_status is ComplianceStatus.INSUFFICIENT_EVIDENCE
    assert case.compliance is None
    qc = next(b for b in case.branches if b.department.value == "qc")
    supply = next(b for b in case.branches if b.department.value == "supply")
    assert qc.status.value == "ok"
    assert supply.status.value == "ok"
    assert result.investigation.gate.passed is False
    assert result.investigation.draft is None
    assert _annex_item(result, "1.7.11").status is ChecklistItemStatus.FAIL
    assert _annex_item(result, "1.7.13").status is ChecklistItemStatus.PASS
    assert result.release_gate.status is ReleaseGateStatus.NOT_READY


@pytest.mark.asyncio
async def test_qc_oos_still_runs_investigation(
    stub_ground_truth,
    stub_investigation_agents,
    stub_department_agents,
    monkeypatch,
    sterility_released,
) -> None:
    stub_ground_truth(make_page_extraction())
    snapshot = lookup_department_snapshot(tenant_id="aurex", batch_id="AX-2291")
    assert snapshot is not None
    dirty = snapshot.model_copy(
        update={
            "lims": LimsSnapshot(
                source_uri=snapshot.lims.source_uri,
                rows=[
                    LimsResultRow(
                        test_code="ASSAY",
                        label="Assay",
                        result_value=80.0,
                        unit="%LC",
                        spec_min=95.0,
                        spec_max=105.0,
                        sample_id="QC-AX-2291-01",
                    )
                ],
            )
        }
    )
    monkeypatch.setattr(
        department_source_mod, "lookup_department_snapshot", lambda **_kwargs: dirty
    )
    result = _gate_package((await _start(_runner(), _case_payload()))[1])
    assert result.investigation.draft is not None
    assert result.investigation.gate.passed is True
    assert any(
        finding.field_name == "ASSAY" for finding in result.investigation.case.findings
    )
    assert _annex_item(result, "1.7.13").status is ChecklistItemStatus.FAIL
    assert _annex_item(result, "1.7.11").status is ChecklistItemStatus.PASS
    assert result.release_gate.status is ReleaseGateStatus.NOT_READY


@pytest.mark.asyncio
async def test_qp_decline_resumes_and_records_confirmation(
    stub_ground_truth,
    stub_investigation_agents,
    stub_department_agents,
    stub_qms_write_agent,
    sterility_released,
) -> None:
    stub_ground_truth(make_page_extraction())
    runner = _runner()
    session_id, events = await _start(runner, _case_payload())
    invocation_id, interrupt_id, _args = _request_input(events)
    reviewed = _reviewed(
        await _resume(
            runner,
            session_id,
            invocation_id,
            interrupt_id,
            {
                "action": "decline",
                "qp_name": "Jane QP",
                "rationale": "Investigation is still open.",
            },
        )
    )
    assert reviewed.decision_authority == "none"
    assert reviewed.qp_confirmation.outcome is QpConfirmationOutcome.DECLINED
    assert reviewed.qp_confirmation.qp_name == "Jane QP"
    assert reviewed.release_package.release_gate.status is ReleaseGateStatus.NOT_READY


@pytest.mark.asyncio
async def test_qp_confirm_on_not_ready_is_rejected(
    stub_ground_truth,
    stub_investigation_agents,
    stub_department_agents,
    stub_qms_write_agent,
    sterility_released,
) -> None:
    stub_ground_truth(make_page_extraction())
    runner = _runner()
    session_id, events = await _start(runner, _case_payload())
    invocation_id, interrupt_id, _args = _request_input(events)
    reviewed = _reviewed(
        await _resume(
            runner,
            session_id,
            invocation_id,
            interrupt_id,
            {
                "action": "confirm",
                "qp_name": "Jane QP",
                "rationale": "Pack looks complete.",
            },
        )
    )
    assert reviewed.qp_confirmation.outcome is QpConfirmationOutcome.REJECTED
    assert reviewed.qp_confirmation.action.value == "confirm"
    assert reviewed.release_package.release_gate.status is ReleaseGateStatus.NOT_READY


def test_extractor_seam_is_unchanged() -> None:
    agent = get_page_extractor()
    assert agent.mode == "single_turn"
    assert agent.include_contents == "none"
    investigator = get_investigation_agent(lambda: None, lambda: None)
    assert investigator.name == "deviation_investigator"
    assert investigator.mode == "single_turn"
    assert investigator.include_contents == "none"
    assert len(investigator.tools) == 2
    challenger = get_challenger_agent()
    assert challenger.name == "investigation_challenger"
    assert challenger.mode == "single_turn"
    assert challenger.include_contents == "none"
    assert challenger.tools == []
