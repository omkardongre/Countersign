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

"""Case Memory: store tenant filtering/redaction, MCP tools, graph nodes."""

from __future__ import annotations

import datetime

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from app.domain.case_memory_mcp import McpCaseMemoryStore
from app.domain.case_memory_models import CaseMemoryQueryResult
from app.domain.case_memory_node import build_case_memory_fact, resolve_prior_art
from app.domain.case_memory_store import (
    InMemoryCaseMemoryStore,
    get_case_memory_store,
)
from app.domain.case_models import CaseComplianceResult
from app.domain.compliance_models import ComplianceStatus
from app.domain.department_tools import (
    CASE_MEMORY_TOOLS,
    PRIOR_ART_QUERY,
    PRIOR_ART_REDACT,
    PRIOR_ART_WRITE,
    CaseMemoryTenantMismatch,
    prior_art_query,
    prior_art_redact,
    prior_art_write,
)
from app.domain.models import ClassifiedCase, DeviationSeverity, DocType
from app.domain.qp_models import (
    QpConfirmationAction,
    QpConfirmationOutcome,
    QpConfirmationRecord,
    QpReviewedPackage,
)
from app.domain.release_models import (
    CaseReleasePackage,
    ReleaseGateResult,
    ReleaseGateStatus,
)
from app.mcp.department_server import build_department_mcp


def _classified(**overrides) -> ClassifiedCase:
    base = {
        "tenant_id": "aurex",
        "batch_id": "AX-2291",
        "product_name": "AXR450 Sterile Injectable",
        "doc_type": DocType.BATCH_RECORD,
        "severity": DeviationSeverity.MAJOR,
        "sterility_hold_seconds": 14 * 24 * 60 * 60,
        "rationale": "Sterility assurance impact triggers MAJOR.",
    }
    base.update(overrides)
    return ClassifiedCase.model_validate(base)


def _case(**overrides) -> CaseComplianceResult:
    return CaseComplianceResult(
        classified=_classified(),
        aggregated_status=ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION,
        **overrides,
    )


# --- InMemoryCaseMemoryStore ---------------------------------------------


@pytest.mark.asyncio
async def test_store_filters_by_tenant() -> None:
    store = InMemoryCaseMemoryStore()
    await store.write_fact(
        tenant_id="aurex",
        case_id="c1",
        batch_id="AX-2291",
        fact="Blend-speed excursion resolved by recalibrating the mixer.",
        source_uri="case://aurex/c1",
        confidence=1.0,
    )
    await store.write_fact(
        tenant_id="other-tenant",
        case_id="c2",
        batch_id="OT-0001",
        fact="Unrelated tenant's deviation.",
        source_uri="case://other-tenant/c2",
        confidence=1.0,
    )
    facts, embedded = await store.query(tenant_id="aurex", query="blend speed")
    assert embedded is False
    assert len(facts) == 1
    assert facts[0].tenant_id == "aurex"


@pytest.mark.asyncio
async def test_store_redact_excludes_fact_from_future_queries() -> None:
    store = InMemoryCaseMemoryStore()
    fact = await store.write_fact(
        tenant_id="aurex",
        case_id="c1",
        batch_id="AX-2291",
        fact="Fact later found to be wrong.",
        source_uri="case://aurex/c1",
        confidence=0.5,
    )
    redacted = await store.redact(
        tenant_id="aurex", fact_id=fact.fact_id, reason="Root cause was reassigned."
    )
    assert redacted.redacted is True
    assert redacted.redacted_reason == "Root cause was reassigned."
    facts, _ = await store.query(tenant_id="aurex", query="wrong")
    assert facts == []


@pytest.mark.asyncio
async def test_store_redact_cross_tenant_fails() -> None:
    store = InMemoryCaseMemoryStore()
    fact = await store.write_fact(
        tenant_id="aurex",
        case_id="c1",
        batch_id="AX-2291",
        fact="A fact.",
        source_uri="case://aurex/c1",
        confidence=1.0,
    )
    with pytest.raises(KeyError):
        await store.redact(
            tenant_id="other-tenant", fact_id=fact.fact_id, reason="wrong tenant"
        )


def test_get_case_memory_store_honors_inmemory_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain import case_memory_store as mod

    monkeypatch.setenv("COUNTERSIGN_CASE_MEMORY_INMEMORY", "1")
    mod.reset_case_memory_store_cache()
    store = get_case_memory_store()
    assert isinstance(store, InMemoryCaseMemoryStore)
    mod.reset_case_memory_store_cache()


def test_get_case_memory_store_honors_bigquery_backend_over_inmemory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``COUNTERSIGN_CASE_MEMORY_BACKEND=bigquery`` wins even if the
    in-memory test flag is also set, matching ``get_lims_source``/
    ``get_em_source``'s explicit-opt-in-per-backend precedence.
    """
    from app.domain import case_memory_store as mod
    from app.domain.case_memory_store_bigquery import BigQueryCaseMemoryStore

    monkeypatch.setenv("COUNTERSIGN_CASE_MEMORY_INMEMORY", "1")
    monkeypatch.setenv("COUNTERSIGN_CASE_MEMORY_BACKEND", "bigquery")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "countersign-505107")
    mod.reset_case_memory_store_cache()
    store = get_case_memory_store()
    assert isinstance(store, BigQueryCaseMemoryStore)
    mod.reset_case_memory_store_cache()


# --- department_tools.py cross-tenant guard ------------------------------


@pytest.mark.asyncio
async def test_prior_art_tools_reject_cross_tenant_calls() -> None:
    with pytest.raises(CaseMemoryTenantMismatch):
        await prior_art_query(
            tenant_id="aurex", query="anything", case_tenant_id="other-tenant"
        )
    with pytest.raises(CaseMemoryTenantMismatch):
        await prior_art_write(
            tenant_id="aurex",
            case_id="c1",
            batch_id="AX-2291",
            fact="fact",
            source_uri="case://aurex/c1",
            confidence=1.0,
            case_tenant_id="other-tenant",
        )
    with pytest.raises(CaseMemoryTenantMismatch):
        await prior_art_redact(
            tenant_id="aurex",
            fact_id="does-not-matter",
            reason="reason",
            case_tenant_id="other-tenant",
        )


@pytest.mark.asyncio
async def test_prior_art_write_then_query_round_trips() -> None:
    written = await prior_art_write(
        tenant_id="aurex",
        case_id="c1",
        batch_id="AX-2291",
        fact="Deviation traced to a mixer calibration drift.",
        source_uri="case://aurex/c1",
        confidence=0.9,
        case_tenant_id="aurex",
    )
    result = await prior_art_query(
        tenant_id="aurex", query="mixer", case_tenant_id="aurex"
    )
    assert isinstance(result, CaseMemoryQueryResult)
    assert any(fact.fact_id == written.fact_id for fact in result.facts)


# --- MCP protocol surface --------------------------------------------------


@pytest.mark.asyncio
async def test_case_memory_tools_are_registered_on_department_mcp() -> None:
    mcp = build_department_mcp()
    async with create_connected_server_and_client_session(mcp) as session:
        listed = await session.list_tools()
    names = {tool.name for tool in listed.tools}
    assert CASE_MEMORY_TOOLS <= names


@pytest.mark.asyncio
async def test_case_memory_mcp_write_and_query_round_trip() -> None:
    mcp = build_department_mcp()
    async with create_connected_server_and_client_session(mcp) as session:
        write_result = await session.call_tool(
            PRIOR_ART_WRITE,
            {
                "tenant_id": "aurex",
                "case_id": "c1",
                "batch_id": "AX-2291",
                "fact": "Deviation traced to a mixer calibration drift.",
                "source_uri": "case://aurex/c1",
                "confidence": 0.9,
                "case_tenant_id": "aurex",
            },
        )
        assert write_result.isError is False
        query_result = await session.call_tool(
            PRIOR_ART_QUERY,
            {"tenant_id": "aurex", "query": "mixer", "case_tenant_id": "aurex"},
        )
        assert query_result.isError is False
        payload = query_result.structuredContent
        assert payload is not None
        assert payload.get("facts") or (payload.get("result") or {}).get("facts")


@pytest.mark.asyncio
async def test_case_memory_mcp_cross_tenant_query_is_tool_error() -> None:
    mcp = build_department_mcp()
    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            PRIOR_ART_QUERY,
            {
                "tenant_id": "aurex",
                "query": "anything",
                "case_tenant_id": "other-tenant",
            },
        )
    assert result.isError is True


@pytest.mark.asyncio
async def test_case_memory_mcp_redact_missing_fact_is_tool_error() -> None:
    mcp = build_department_mcp()
    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            PRIOR_ART_REDACT,
            {
                "tenant_id": "aurex",
                "fact_id": "no-such-fact",
                "reason": "test",
                "case_tenant_id": "aurex",
            },
        )
    assert result.isError is True


# --- McpCaseMemoryStore (Runtime-side async client) ------------------------


@pytest.mark.asyncio
async def test_mcp_case_memory_store_round_trips() -> None:
    store = McpCaseMemoryStore()
    written = await store.write_fact_async(
        tenant_id="aurex",
        case_id="c1",
        batch_id="AX-2291",
        fact="Deviation traced to a mixer calibration drift.",
        source_uri="case://aurex/c1",
        confidence=0.9,
    )
    result = await store.query_async(tenant_id="aurex", query="mixer")
    assert any(fact.fact_id == written.fact_id for fact in result.facts)


@pytest.mark.asyncio
async def test_mcp_case_memory_store_empty_result_is_not_embedded() -> None:
    store = McpCaseMemoryStore()
    result = await store.query_async(tenant_id="aurex", query="nothing written yet")
    assert result.facts == []
    assert result.query_embedded is False


# --- retrieve_prior_art node ------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_prior_art_attaches_facts() -> None:
    await prior_art_write(
        tenant_id="aurex",
        case_id="prior-case",
        batch_id="AX-2000",
        fact="AXR450 Sterile Injectable: prior blend-speed excursion.",
        source_uri="case://aurex/prior-case",
        confidence=0.8,
        case_tenant_id="aurex",
    )
    case = _case()
    updated = await resolve_prior_art(case)
    assert updated.prior_art
    assert updated.prior_art[0].tenant_id == "aurex"


@pytest.mark.asyncio
async def test_retrieve_prior_art_is_advisory_on_store_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp.client import DepartmentToolCallError

    async def _boom(self, **_kwargs):
        raise DepartmentToolCallError("Case Memory unavailable")

    monkeypatch.setattr(McpCaseMemoryStore, "query_async", _boom)
    case = _case()
    updated = await resolve_prior_art(case)
    assert updated.prior_art == []
    assert updated == case


@pytest.mark.asyncio
async def test_retrieve_prior_art_does_not_mask_a_real_bug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A programming error must fail loudly, not be treated as an outage."""

    async def _boom(self, **_kwargs):
        raise TypeError("bad call site")

    monkeypatch.setattr(McpCaseMemoryStore, "query_async", _boom)
    with pytest.raises(TypeError):
        await resolve_prior_art(_case())


# --- build_case_memory_fact -------------------------------------------------


def _reviewed(
    outcome: QpConfirmationOutcome, action: QpConfirmationAction
) -> QpReviewedPackage:
    from app.domain.investigation_models import (
        InvestigationCaseResult,
        InvestigationGateResult,
    )

    investigation = InvestigationCaseResult(
        case=_case(),
        gate=InvestigationGateResult(passed=True),
    )
    package = CaseReleasePackage(
        investigation=investigation,
        release_gate=ReleaseGateResult(
            status=ReleaseGateStatus.READY_FOR_QP_REVIEW,
            spec_id="annex16_v1",
            spec_version="1",
            spec_sha256="0" * 64,
            items=[],
        ),
    )
    return QpReviewedPackage(
        release_package=package,
        qp_confirmation=QpConfirmationRecord(
            action=action,
            outcome=outcome,
            qp_name="Jane QP",
            rationale="test",
            tenant_id="aurex",
            case_id=package.investigation.case.classified.case_id,
            batch_id="AX-2291",
            gate_status=ReleaseGateStatus.READY_FOR_QP_REVIEW,
            recorded_at=datetime.datetime.now(datetime.UTC),
        ),
    )


def test_build_case_memory_fact_confirmed_produces_a_fact() -> None:
    reviewed = _reviewed(
        QpConfirmationOutcome.CONFIRMED, QpConfirmationAction.CONFIRM
    )
    fact = build_case_memory_fact(reviewed)
    assert fact is not None
    assert "confirmed" in fact


def test_build_case_memory_fact_rejected_is_not_a_settled_fact() -> None:
    reviewed = _reviewed(
        QpConfirmationOutcome.REJECTED, QpConfirmationAction.CONFIRM
    )
    assert build_case_memory_fact(reviewed) is None


def test_build_case_memory_fact_declined_includes_rationale() -> None:
    reviewed = _reviewed(
        QpConfirmationOutcome.DECLINED, QpConfirmationAction.DECLINE
    )
    fact = build_case_memory_fact(reviewed)
    assert fact is not None
    assert "declined" in fact


# --- record_case_memory idempotency on resume -------------------------------


@pytest.mark.asyncio
async def test_record_case_memory_rerun_overwrites_not_duplicates() -> None:
    """A crash-and-resume rerun of this node must not double-write the fact."""
    from app.domain.case_memory_node import record_case_memory_impl

    reviewed = _reviewed(
        QpConfirmationOutcome.CONFIRMED, QpConfirmationAction.CONFIRM
    )
    await record_case_memory_impl(reviewed)
    await record_case_memory_impl(reviewed)  # simulated resume rerun

    store = get_case_memory_store()
    facts, _ = await store.query(tenant_id="aurex", query="confirmed")
    matching = [f for f in facts if f.case_id == reviewed.qp_confirmation.case_id]
    assert len(matching) == 1


# --- prior_art_redact operator CLI -------------------------------------------


def test_redact_cli_calls_the_mcp_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain import case_memory_redact

    captured = {}

    async def _fake_call(name, arguments):
        captured["name"] = name
        captured["arguments"] = arguments
        return {"fact_id": arguments["fact_id"], "redacted": True}

    monkeypatch.setattr(case_memory_redact, "call_case_memory_tool", _fake_call)
    exit_code = case_memory_redact.main(
        ["--tenant-id", "aurex", "--fact-id", "f1", "--reason", "wrong root cause"]
    )
    assert exit_code == 0
    assert captured["name"] == PRIOR_ART_REDACT
    assert captured["arguments"]["tenant_id"] == "aurex"
    assert captured["arguments"]["case_tenant_id"] == "aurex"
