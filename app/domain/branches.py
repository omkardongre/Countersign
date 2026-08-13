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

"""Parallel department collectors. Each always yields Event.output for JoinNode."""

from __future__ import annotations

from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.workflow import node

from app.domain.compliance import resolve_compliance_result
from app.domain.department_checks import check_coa, check_em, check_lims
from app.domain.department_models import (
    BranchOutcome,
    BranchStatus,
    CaseEvidenceBundle,
    CoaSnapshot,
    Department,
    EmSnapshot,
    LimsSnapshot,
)
from app.domain.department_tools import COA_FETCH, EM_QUERY, LIMS_QUERY
from app.domain.evidence_models import (
    ExtractedEvidence,
    IngestedArtifact,
    ValidatedEvidence,
)
from app.domain.evidence_validation import validate_page_evidence
from app.domain.extraction import extract_page_evidence
from app.domain.ingest import decode_and_verify_payload, ingest_evidence_artifact
from app.mcp.client import call_department_tool


def _exc_message(exc: BaseException) -> str:
    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        messages.append(str(current))
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return " | ".join(messages)


def _bundle(node_input: CaseEvidenceBundle | dict) -> CaseEvidenceBundle:
    if isinstance(node_input, CaseEvidenceBundle):
        return node_input
    return CaseEvidenceBundle.model_validate(node_input)


def _failed(department: Department, error: str) -> Event:
    return Event(
        output=BranchOutcome(
            department=department,
            status=BranchStatus.FAILED,
            error=error,
        ).model_dump(mode="json")
    )


@node(name="collect_manufacturing", rerun_on_resume=True)
async def collect_manufacturing(
    ctx: Context, node_input: CaseEvidenceBundle
) -> Event:
    """BMR ingest → extract → validate → compliance. Failsafe Event on error."""
    try:
        bundle = _bundle(node_input)
        # MIME/hash fail closed here so JoinNode gets the integrity message,
        # not ADK's wrapped "Dynamic node ... failed".
        decode_and_verify_payload(bundle.bmr)
        artifact = IngestedArtifact.model_validate(
            await ctx.run_node(ingest_evidence_artifact, node_input=bundle.bmr)
        )
        extracted = ExtractedEvidence.model_validate(
            await ctx.run_node(extract_page_evidence, node_input=artifact)
        )
        validated = ValidatedEvidence.model_validate(
            await ctx.run_node(validate_page_evidence, node_input=extracted)
        )
        compliance = resolve_compliance_result(validated)
        return Event(
            output=BranchOutcome(
                department=Department.MANUFACTURING,
                status=BranchStatus.OK,
                source_uris=[bundle.bmr.source_uri],
                findings=list(compliance.findings),
                manufacturing_compliance=compliance,
            ).model_dump(mode="json")
        )
    except Exception as exc:
        return _failed(Department.MANUFACTURING, _exc_message(exc))


@node(name="collect_qc")
async def collect_qc(node_input: CaseEvidenceBundle) -> Event:
    """LIMS + EM via named MCP tools ``lims_query`` and ``em_query``."""
    try:
        bundle = _bundle(node_input)
        lims = LimsSnapshot.model_validate(
            await call_department_tool(
                LIMS_QUERY,
                tenant_id=bundle.tenant_id,
                batch_id=bundle.batch_id,
                lifecycle_state=bundle.lifecycle_state,
                case_tenant_id=bundle.tenant_id,
            )
        )
        em = EmSnapshot.model_validate(
            await call_department_tool(
                EM_QUERY,
                tenant_id=bundle.tenant_id,
                batch_id=bundle.batch_id,
                lifecycle_state=bundle.lifecycle_state,
                case_tenant_id=bundle.tenant_id,
            )
        )
        findings = check_lims(lims) + check_em(em)
        return Event(
            output=BranchOutcome(
                department=Department.QC,
                status=BranchStatus.OK,
                source_uris=[lims.source_uri, em.source_uri],
                findings=findings,
            ).model_dump(mode="json")
        )
    except Exception as exc:
        return _failed(Department.QC, _exc_message(exc))


@node(name="collect_supply")
async def collect_supply(node_input: CaseEvidenceBundle) -> Event:
    """Supplier CoA via named MCP tool ``coa_fetch``."""
    try:
        bundle = _bundle(node_input)
        coa = CoaSnapshot.model_validate(
            await call_department_tool(
                COA_FETCH,
                tenant_id=bundle.tenant_id,
                batch_id=bundle.batch_id,
                lifecycle_state=bundle.lifecycle_state,
                case_tenant_id=bundle.tenant_id,
            )
        )
        findings = check_coa(coa)
        return Event(
            output=BranchOutcome(
                department=Department.SUPPLY,
                status=BranchStatus.OK,
                source_uris=[coa.source_uri],
                findings=findings,
            ).model_dump(mode="json")
        )
    except Exception as exc:
        return _failed(Department.SUPPLY, _exc_message(exc))
