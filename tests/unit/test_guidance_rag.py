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

"""Doc 12 T1-5: controlled-SOP / public-guidance RAG.

Covers the manifest cross-check, tenant isolation, the mandatory-SOP
citation gate, and the MCP protocol surface — the same "manifest-verified,
never trust retrieval alone" and "tenant pre-filter, not post-filter"
disciplines already exercised for Case Memory.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from app.domain.case_models import CaseComplianceResult
from app.domain.compliance_models import ComplianceStatus
from app.domain.department_models import BranchOutcome, BranchStatus, Department
from app.domain.department_tools import (
    CONTROLLED_GUIDANCE_QUERY,
    GUIDANCE_TOOLS,
    ControlledGuidanceTenantMismatch,
    controlled_guidance_query,
)
from app.domain.guidance_manifest import load_guidance_manifest
from app.domain.guidance_models import GuidanceCorpus
from app.domain.guidance_node import resolve_controlled_guidance
from app.domain.guidance_source import (
    InMemoryGuidanceSource,
    get_guidance_source,
    reset_guidance_source_cache,
    sop_corpus_configured,
)
from app.domain.investigation_brief import allowed_source_uris
from app.domain.investigation_gate import guidance_citation_failures
from app.domain.investigation_models import CitedClaim, InvestigationDraft
from app.domain.models import ClassifiedCase, DeviationSeverity, DocType
from app.mcp.department_server import build_department_mcp


def _classified(**overrides) -> ClassifiedCase:
    base = {
        "tenant_id": "aurex",
        "batch_id": "AX-2291",
        "product_name": "AXR450 Sterile Injectable",
        "doc_type": DocType.BATCH_RECORD,
        "severity": DeviationSeverity.MAJOR,
        "evidence_refs": ["drive://synthetic/bmr/AX-2291/page-14"],
        "rationale": "Sterility assurance impact triggers MAJOR.",
    }
    base.update(overrides)
    return ClassifiedCase.model_validate(base)


def _case(**overrides) -> CaseComplianceResult:
    return CaseComplianceResult(
        classified=_classified(),
        aggregated_status=ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION,
        branches=[
            BranchOutcome(department=Department.MANUFACTURING, status=BranchStatus.OK),
            BranchOutcome(department=Department.QC, status=BranchStatus.OK),
            BranchOutcome(
                department=Department.ENVIRONMENTAL_MONITORING, status=BranchStatus.OK
            ),
            BranchOutcome(department=Department.SUPPLY, status=BranchStatus.OK),
        ],
        **overrides,
    )


def _draft(case: CaseComplianceResult, *, sop_uri: str | None) -> InvestigationDraft:
    from app.domain.investigation_models import RootCauseHypothesis
    from app.domain.master_data import RootCauseCategory

    source_uris = [case.classified.evidence_refs[0]]
    if sop_uri:
        source_uris.append(sop_uri)
    return InvestigationDraft(
        tenant_id=case.classified.tenant_id,
        case_id=case.classified.case_id,
        batch_id=case.classified.batch_id,
        hypotheses=[
            RootCauseHypothesis(
                category=RootCauseCategory.METHOD,
                statement="Blend-speed excursion is the leading process deviation.",
                supporting_claims=[
                    CitedClaim(claim="Cited case + SOP evidence.", source_uris=source_uris)
                ],
                confidence=0.6,
            )
        ],
        decision_authority="none",
    )


# --- manifest -------------------------------------------------------------


def test_manifest_loads_and_pins_sha256() -> None:
    manifest = load_guidance_manifest()
    assert manifest.manifest_id == "controlled_guidance_manifest"
    assert len(manifest.manifest_sha256) == 64
    sop = manifest.by_doc_id("SOP-QA-014")
    assert sop is not None
    assert sop.corpus is GuidanceCorpus.CONTROLLED_SOP
    assert sop.tenant_id == "aurex"
    assert sop.in_force is True


# --- InMemoryGuidanceSource: tenant isolation + manifest cross-check ------


@pytest.mark.asyncio
async def test_inmemory_source_filters_controlled_sop_by_tenant() -> None:
    source = InMemoryGuidanceSource()
    citations, configured = await source.query(
        tenant_id="other-tenant",
        corpus=GuidanceCorpus.CONTROLLED_SOP,
        query="",
    )
    assert configured is True
    assert citations == []  # SOP-QA-014 belongs to "aurex", not "other-tenant".


@pytest.mark.asyncio
async def test_inmemory_source_returns_public_guidance_for_any_tenant() -> None:
    source = InMemoryGuidanceSource()
    citations, configured = await source.query(
        tenant_id="other-tenant",
        corpus=GuidanceCorpus.PUBLIC_GUIDANCE,
        query="",
    )
    assert configured is True
    assert any(c.doc_id == "EU-GMP-ANNEX-1-2022" for c in citations)


@pytest.mark.asyncio
async def test_inmemory_source_matches_controlled_sop_for_owning_tenant() -> None:
    source = InMemoryGuidanceSource()
    citations, _ = await source.query(
        tenant_id="aurex", corpus=GuidanceCorpus.CONTROLLED_SOP, query=""
    )
    assert any(c.doc_id == "SOP-QA-014" for c in citations)
    assert all(c.tenant_id == "aurex" for c in citations)


# --- department_tools.py cross-tenant guard --------------------------------


@pytest.mark.asyncio
async def test_controlled_guidance_query_rejects_cross_tenant_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COUNTERSIGN_GUIDANCE_INMEMORY", "1")
    reset_guidance_source_cache()
    with pytest.raises(ControlledGuidanceTenantMismatch):
        await controlled_guidance_query(
            tenant_id="aurex",
            corpus="controlled_sop",
            query="anything",
            case_tenant_id="other-tenant",
        )
    reset_guidance_source_cache()


@pytest.mark.asyncio
async def test_controlled_guidance_query_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COUNTERSIGN_GUIDANCE_INMEMORY", "1")
    reset_guidance_source_cache()
    result = await controlled_guidance_query(
        tenant_id="aurex",
        corpus="controlled_sop",
        query="",
        case_tenant_id="aurex",
    )
    assert result.configured is True
    assert any(c.doc_id == "SOP-QA-014" for c in result.citations)
    reset_guidance_source_cache()


def test_get_guidance_source_honors_inmemory_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COUNTERSIGN_GUIDANCE_INMEMORY", "1")
    reset_guidance_source_cache()
    assert isinstance(get_guidance_source(), InMemoryGuidanceSource)
    reset_guidance_source_cache()


def test_sop_corpus_configured_reflects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COUNTERSIGN_GUIDANCE_INMEMORY", raising=False)
    monkeypatch.delenv("COUNTERSIGN_GUIDANCE_SOP_CORPUS", raising=False)
    assert sop_corpus_configured() is False
    monkeypatch.setenv("COUNTERSIGN_GUIDANCE_SOP_CORPUS", "projects/p/locations/l/ragCorpora/c")
    assert sop_corpus_configured() is True


# --- MCP protocol surface --------------------------------------------------


@pytest.mark.asyncio
async def test_guidance_tool_is_registered_on_department_mcp() -> None:
    mcp = build_department_mcp()
    async with create_connected_server_and_client_session(mcp) as session:
        listed = await session.list_tools()
    names = {tool.name for tool in listed.tools}
    assert GUIDANCE_TOOLS <= names


@pytest.mark.asyncio
async def test_guidance_mcp_cross_tenant_query_is_tool_error() -> None:
    mcp = build_department_mcp()
    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            CONTROLLED_GUIDANCE_QUERY,
            {
                "tenant_id": "aurex",
                "corpus": "controlled_sop",
                "query": "anything",
                "case_tenant_id": "other-tenant",
            },
        )
    assert result.isError is True


@pytest.mark.asyncio
async def test_guidance_mcp_invalid_corpus_is_tool_error() -> None:
    mcp = build_department_mcp()
    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            CONTROLLED_GUIDANCE_QUERY,
            {
                "tenant_id": "aurex",
                "corpus": "not_a_real_corpus",
                "query": "anything",
                "case_tenant_id": "aurex",
            },
        )
    assert result.isError is True


# --- graph node: fail-open on outage, fail-loud on bugs --------------------


@pytest.mark.asyncio
async def test_resolve_controlled_guidance_attaches_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COUNTERSIGN_MCP_INPROCESS", "1")
    monkeypatch.setenv("COUNTERSIGN_GUIDANCE_INMEMORY", "1")
    reset_guidance_source_cache()
    case = _case()
    updated = await resolve_controlled_guidance(case)
    assert any(c.doc_id == "SOP-QA-014" for c in updated.controlled_guidance)
    assert any(c.doc_id == "EU-GMP-ANNEX-1-2022" for c in updated.controlled_guidance)
    reset_guidance_source_cache()


# --- investigation_brief.py: only in-force citations are citeable ---------


def test_allowed_source_uris_excludes_superseded_guidance() -> None:
    case = _case()
    from app.domain.guidance_models import GuidanceCitation

    in_force = GuidanceCitation(
        tenant_id="aurex",
        corpus=GuidanceCorpus.CONTROLLED_SOP,
        doc_id="SOP-QA-014",
        title="Deviation Investigation SOP",
        version="4.0",
        effective_date=case.classified.classified_at.date(),
        approved=True,
        superseded=False,
        source_uri="https://drive.google.com/synthetic/qms/sop-qa-014",
        text="excerpt",
    )
    stale = in_force.model_copy(
        update={
            "citation_id": "stale",
            "superseded": True,
            "source_uri": "https://drive.google.com/synthetic/qms/sop-qa-013",
        }
    )
    case = case.model_copy(update={"controlled_guidance": [in_force, stale]})
    uris = allowed_source_uris(case)
    assert in_force.source_uri in uris
    assert stale.source_uri not in uris


# --- investigation_gate.py: mandatory controlled-SOP citation -------------


def test_guidance_gate_is_a_noop_when_sop_corpus_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COUNTERSIGN_GUIDANCE_INMEMORY", raising=False)
    monkeypatch.delenv("COUNTERSIGN_GUIDANCE_SOP_CORPUS", raising=False)
    case = _case()
    draft = _draft(case, sop_uri=None)
    assert guidance_citation_failures(case, draft) == []


def test_guidance_gate_blocks_when_no_in_force_sop_citation_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COUNTERSIGN_GUIDANCE_INMEMORY", "1")
    case = _case()  # no controlled_guidance attached at all
    draft = _draft(case, sop_uri=None)
    failures = guidance_citation_failures(case, draft)
    assert any("No in-force controlled SOP citation" in f for f in failures)


@pytest.mark.asyncio
async def test_guidance_gate_blocks_when_sop_available_but_not_cited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COUNTERSIGN_MCP_INPROCESS", "1")
    monkeypatch.setenv("COUNTERSIGN_GUIDANCE_INMEMORY", "1")
    reset_guidance_source_cache()
    case = _case()
    updated = await resolve_controlled_guidance(case)
    draft = _draft(updated, sop_uri=None)  # never cites the SOP source_uri
    failures = guidance_citation_failures(updated, draft)
    assert any("cites no in-force controlled SOP" in f for f in failures)
    reset_guidance_source_cache()


@pytest.mark.asyncio
async def test_guidance_gate_passes_when_sop_is_cited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COUNTERSIGN_MCP_INPROCESS", "1")
    monkeypatch.setenv("COUNTERSIGN_GUIDANCE_INMEMORY", "1")
    reset_guidance_source_cache()
    case = _case()
    updated = await resolve_controlled_guidance(case)
    sop_uri = next(
        c.source_uri
        for c in updated.controlled_guidance
        if c.corpus is GuidanceCorpus.CONTROLLED_SOP
    )
    draft = _draft(updated, sop_uri=sop_uri)
    assert guidance_citation_failures(updated, draft) == []
    reset_guidance_source_cache()
