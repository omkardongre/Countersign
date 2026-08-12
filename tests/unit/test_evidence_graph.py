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

"""End-to-end tests for the evidence-extraction graph.

The transcriber is replaced with a stub node returning known ground truth. That
is the point of the injection seam in ``app.domain.extraction``: everything
around the model — artifact persistence, hash and tenant re-verification,
provenance stamping, validation, confidence routing, the audit event — is tested
deterministically, with no API key and no network. Whether Gemini reads a
particular smudged digit correctly is a separate, model-level evaluation
question; whether the pipeline around it is trustworthy is settled here.
"""

import base64
import hashlib
import json

import pytest
from google.adk.apps import App
from google.adk.runners import InMemoryRunner
from google.adk.workflow import node
from google.genai import types

from app.domain import extraction
from app.domain.compliance_models import ComplianceResult, ComplianceStatus
from app.domain.evidence_graph import build_evidence_extraction_workflow
from app.domain.evidence_models import (
    EvidenceStatus,
    FieldStatus,
    ModelFieldReading,
    PageExtraction,
)
from app.domain.extraction import (
    EXTRACTOR_AGENT_NAME,
    EXTRACTOR_MODEL,
    build_extractor_instruction,
    get_page_extractor,
)
from app.domain.master_data import load_document_field_spec
from tests.unit.evidence_fixtures import (
    SYNTHETIC_PAGE_BYTES,
    make_page_extraction,
)

_APP_NAME = "test_evidence_app"
_USER_ID = "test-qa-user"


def _stub_extractor(extraction_result: PageExtraction):
    """Builds a node standing in for the multimodal transcriber."""

    @node(name="stub_page_extractor")
    def stub_page_extractor(node_input) -> PageExtraction:
        # Asserts the node actually handed the document to the extractor rather
        # than only a text description of it.
        assert isinstance(node_input, types.Content)
        assert any(
            part.inline_data is not None or part.file_data is not None
            for part in node_input.parts or []
        )
        return extraction_result

    return stub_page_extractor


@pytest.fixture
def stub_ground_truth(monkeypatch):
    """Installs a stub transcriber; returns a setter for its output."""

    def _install(extraction_result: PageExtraction) -> None:
        monkeypatch.setattr(
            extraction,
            "get_page_extractor",
            lambda spec=None: _stub_extractor(extraction_result),
        )

    return _install


def _runner() -> InMemoryRunner:
    app = App(name=_APP_NAME, root_agent=build_evidence_extraction_workflow())
    return InMemoryRunner(app=app, app_name=_APP_NAME)


def _request_payload(**overrides) -> dict:
    payload = {
        "tenant_id": "aurex",
        "case_id": "case-evidence-1",
        "batch_id": "AX-2291",
        "source_uri": "drive://synthetic/bmr/AX-2291/page-14",
        "mime_type": "image/png",
        "content_base64": base64.b64encode(SYNTHETIC_PAGE_BYTES).decode(),
        "page_count": 1,
    }
    payload.update(overrides)
    return payload


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


def _final_result(events: list) -> ComplianceResult:
    outputs = [event.output for event in events if event.output is not None]
    assert outputs, "workflow produced no node output"
    return ComplianceResult.model_validate(outputs[-1])


@pytest.mark.asyncio
async def test_clean_page_flows_end_to_end_and_is_accepted(
    stub_ground_truth,
) -> None:
    stub_ground_truth(make_page_extraction())
    runner = _runner()

    result = _final_result(await _run(runner, _request_payload()))
    evidence = result.source_evidence

    assert evidence.status is EvidenceStatus.ACCEPTED
    assert evidence.tenant_id == "aurex"
    assert evidence.batch_id == "AX-2291"
    assert evidence.review_queue == []
    assert evidence.extractor_agent == EXTRACTOR_AGENT_NAME
    assert evidence.extractor_model == EXTRACTOR_MODEL
    assert evidence.decision_authority == "none"
    # The ground-truth page is AXR450, which has an approved trend baseline,
    # and every value in it sits inside that baseline -- so the case reaches
    # the end of the graph with nothing outstanding at all.
    assert result.status is ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION
    assert result.findings == []
    assert result.trend_baseline_id == "axr450_blending_trend_baseline"
    assert result.decision_authority == "none"


@pytest.mark.asyncio
async def test_artifact_is_persisted_hashed_and_tenant_scoped(
    stub_ground_truth,
) -> None:
    stub_ground_truth(make_page_extraction())
    runner = _runner()

    result = _final_result(await _run(runner, _request_payload())).source_evidence

    expected_digest = hashlib.sha256(SYNTHETIC_PAGE_BYTES).hexdigest()
    assert result.artifact_sha256 == expected_digest
    assert result.artifact_filename.startswith("evidence__aurex__case-evidence-1__")

    stored = await runner.artifact_service.load_artifact(
        app_name=_APP_NAME,
        user_id=_USER_ID,
        session_id=(
            await runner.session_service.list_sessions(
                app_name=_APP_NAME, user_id=_USER_ID
            )
        ).sessions[0].id,
        filename=result.artifact_filename,
        version=result.artifact_version,
    )
    assert stored is not None
    assert stored.inline_data is not None
    assert stored.inline_data.data == SYNTHETIC_PAGE_BYTES


@pytest.mark.asyncio
async def test_every_fact_is_anchored_to_the_persisted_artifact(
    stub_ground_truth,
) -> None:
    stub_ground_truth(make_page_extraction())
    runner = _runner()

    result = _final_result(await _run(runner, _request_payload())).source_evidence

    for field in result.fields:
        assert field.anchor is not None
        assert field.anchor.artifact_filename == result.artifact_filename
        assert field.anchor.artifact_sha256 == result.artifact_sha256
        assert field.anchor.source_uri == "drive://synthetic/bmr/AX-2291/page-14"


@pytest.mark.asyncio
async def test_illegible_critical_field_routes_to_human_review(
    stub_ground_truth,
) -> None:
    illegible = ModelFieldReading(
        field_name="operator_initials",
        value_text="JMR",
        confidence=0.42,
        page_number=1,
        transcribed_context="Performed by: JMR",
        legibility_note="Initials overwritten; second character ambiguous.",
    )
    stub_ground_truth(
        make_page_extraction(replace={"operator_initials": illegible})
    )
    runner = _runner()

    result = _final_result(await _run(runner, _request_payload())).source_evidence
    field = next(f for f in result.fields if f.field_name == "operator_initials")

    assert result.status is EvidenceStatus.HUMAN_REVIEW_REQUIRED
    assert result.review_queue == ["operator_initials"]
    assert field.status is FieldStatus.REVIEW_REQUIRED
    assert field.legibility_note.startswith("Initials overwritten")
    assert field.anchor is not None  # reviewer can find it on the page


@pytest.mark.asyncio
async def test_invented_field_is_discarded_at_the_boundary(
    stub_ground_truth,
) -> None:
    invented = ModelFieldReading(
        field_name="operator_mood",
        value_text="rushed",
        confidence=0.99,
        page_number=1,
        transcribed_context="operator_mood: rushed",
    )
    stub_ground_truth(make_page_extraction(add=[invented]))
    runner = _runner()

    result = _final_result(await _run(runner, _request_payload())).source_evidence

    assert "operator_mood" not in {f.field_name for f in result.fields}
    assert any(
        "unspecified_field_discarded" in finding
        for finding in result.record_findings
    )


@pytest.mark.asyncio
async def test_conflicting_transcriptions_are_escalated_not_resolved(
    stub_ground_truth,
) -> None:
    """Two readings of one field is an ambiguity for a human, not a tiebreak."""
    duplicate = ModelFieldReading(
        field_name="blend_speed_rpm",
        value_text="13.5",
        unit_text="rpm",
        confidence=0.97,
        page_number=1,
        transcribed_context="Speed: 13.5 rpm",
    )
    stub_ground_truth(make_page_extraction(add=[duplicate]))
    runner = _runner()

    result = _final_result(await _run(runner, _request_payload())).source_evidence
    field = next(f for f in result.fields if f.field_name == "blend_speed_rpm")

    assert field.status is FieldStatus.MISSING
    assert result.status is EvidenceStatus.HUMAN_REVIEW_REQUIRED
    assert "blend_speed_rpm" in result.review_queue
    # The finding must name the actual disagreement, not claim the extractor
    # saw nothing -- those are different diagnoses for a reviewer.
    assert any(
        "conflicting_transcription" in finding for finding in field.findings
    )
    assert "11.5" in field.findings[0] and "13.5" in field.findings[0]


@pytest.mark.asyncio
async def test_conflicting_and_unspecified_fields_are_both_handled(
    stub_ground_truth,
) -> None:
    """A field name that is both duplicated and outside the approved spec."""
    invented_once = ModelFieldReading(
        field_name="operator_mood",
        value_text="rushed",
        confidence=0.9,
        page_number=1,
        transcribed_context="operator_mood: rushed",
    )
    invented_again = invented_once.model_copy(update={"value_text": "calm"})
    stub_ground_truth(
        make_page_extraction(add=[invented_once, invented_again])
    )
    runner = _runner()

    result = _final_result(await _run(runner, _request_payload())).source_evidence

    # Discarded as unspecified before conflict tracking ever sees it -- an
    # invented field must not surface as a reviewable conflict either.
    assert "operator_mood" not in {f.field_name for f in result.fields}
    assert not any("operator_mood" in q for q in result.review_queue)
    assert any(
        "unspecified_field_discarded" in finding
        for finding in result.record_findings
    )


@pytest.mark.asyncio
async def test_disallowed_mime_type_terminates_the_graph(stub_ground_truth) -> None:
    """A refused integrity control raises; it is not a value to route around."""
    stub_ground_truth(make_page_extraction())
    runner = _runner()

    with pytest.raises(Exception, match="not an accepted evidence type"):
        await _run(runner, _request_payload(mime_type="application/zip"))


@pytest.mark.asyncio
async def test_pinning_a_wrong_spec_version_terminates_the_graph(
    stub_ground_truth,
) -> None:
    stub_ground_truth(make_page_extraction())
    runner = _runner()

    with pytest.raises(Exception, match="approved spec in force"):
        await _run(runner, _request_payload(spec_version_expected="0.0.1"))


@pytest.mark.asyncio
async def test_out_of_range_reading_reaches_the_compliance_result(
    stub_ground_truth,
) -> None:
    """An OOS finding must survive the full ingest-extract-validate-comply
    chain, not just the validation node in isolation."""
    out_of_range = ModelFieldReading(
        field_name="blend_speed_rpm",
        value_text="18.0",
        unit_text="rpm",
        confidence=0.98,
        page_number=1,
        transcribed_context="Speed: 18.0 rpm",
    )
    stub_ground_truth(
        make_page_extraction(replace={"blend_speed_rpm": out_of_range})
    )
    runner = _runner()

    result = _final_result(await _run(runner, _request_payload()))

    assert result.status is ComplianceStatus.INSUFFICIENT_EVIDENCE
    oos_findings = [
        f for f in result.findings if f.field_name == "blend_speed_rpm"
    ]
    assert len(oos_findings) == 1
    assert oos_findings[0].finding_type.value == "out_of_specification"
    assert oos_findings[0].anchor is not None
    assert oos_findings[0].anchor.artifact_sha256 == result.artifact_sha256


def test_extractor_is_configured_as_a_bounded_single_turn_transcriber() -> None:
    agent = get_page_extractor()

    assert agent.name == EXTRACTOR_AGENT_NAME
    assert agent.mode == "single_turn"
    # No conversation history: an externally-sourced document cannot steer the
    # reading of the next one.
    assert agent.include_contents == "none"
    assert agent.output_schema is PageExtraction
    assert not agent.tools


def test_extractor_instruction_is_generated_from_approved_master_data() -> None:
    spec = load_document_field_spec()
    instruction = build_extractor_instruction(spec)

    for field_name in spec.field_names:
        assert field_name in instruction
    # Acceptance ranges are withheld: telling a transcriber the expected range
    # invites it to read an out-of-range value as an in-range one, and that
    # value is usually the deviation being investigated.
    assert "8.0" not in instruction
    assert "14.0" not in instruction
