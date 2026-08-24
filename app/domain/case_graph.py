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

"""Deviation case graph: intake → departments → investigation → sterility hold → Annex 16 → QP.

ADK fan-out/fan-in (``workflows/fan_out_fan_in``): after classify, four
department collectors run in parallel and a ``JoinNode`` waits for all of
them. Each collector always emits ``Event.output`` so a failed branch cannot
stall the join (ADK JoinNode caution).

    START -> intake_and_bind_evidence
          -> (collect_manufacturing, collect_qc,
              collect_environmental_monitoring, collect_supply)
          -> join_departments
          -> reconcile_departments
          -> retrieve_prior_art
          -> retrieve_controlled_guidance
          -> draft_investigation
          -> challenge_investigation
          -> gate_investigation
          -> await_sterility_incubation
          -> evaluate_annex16_release_gate
          -> confirm_qp
          -> record_case_memory
          -> file_qms_report

Manufacturing, QC lab, environmental monitoring, and supply are each a real
single-turn ``Agent`` bound to its own MCP-backed tool(s)
(``app/domain/branches.py``), per the target architecture in
``final_reseach/10_gap_analysis_and_win_plan.md`` section 4.1. Each agent
decides to call its own tool; deterministic checks
(``resolve_compliance_result``/``check_lims``/``check_em``/``check_coa``) run
only on the tool's captured typed snapshot, never on the agent's own text.
Investigation is a single-turn LLM with a real prior-art tool; challenger is
single-turn with no tools by design (clean context). Citation and Annex 16
gates are code. Sterility incubation is external store state plus a
``RequestInput`` checkpoint (``rerun_on_resume=True``); a scheduler tick
marks the store complete and ADK resume continues. QP sign-off is a
``RequestInput`` pause on ``confirm_qp`` (``rerun_on_resume=True``).
After the QP has acted, ``qa_qms_write_agent`` (``qms_report_agent.py``) —
the 8th fleet member and the only agent permitted to mutate an external
record — files the investigation report and Annex 16 package as a real
Google Doc. Nothing in this graph certifies or releases a batch.
"""

from __future__ import annotations

from typing import Any

from google.adk.events.event import Event
from google.adk.workflow import START, JoinNode, Workflow, node

from app.domain.branches import (
    collect_environmental_monitoring,
    collect_manufacturing,
    collect_qc,
    collect_supply,
)
from app.domain.case_memory_node import record_case_memory, retrieve_prior_art
from app.domain.case_models import CaseComplianceResult, DeviationCaseRequest
from app.domain.challenger import challenge_investigation
from app.domain.compliance_models import ComplianceStatus
from app.domain.department_models import (
    BranchOutcome,
    BranchStatus,
    CaseEvidenceBundle,
    Department,
)
from app.domain.evidence_models import EvidenceIngestRequest
from app.domain.guidance_node import retrieve_controlled_guidance
from app.domain.ingest import EvidenceIntegrityError
from app.domain.intake import classify_intake
from app.domain.investigation import draft_investigation
from app.domain.investigation_gate import gate_investigation
from app.domain.models import ClassifiedCase
from app.domain.qms_report_agent import file_qms_report
from app.domain.qp_confirmation import confirm_qp
from app.domain.qp_models import QpReviewedPackage
from app.domain.release_gate import evaluate_annex16_release_gate_node
from app.domain.sterility_wait import await_sterility_incubation
from app.domain.workflow_meta import (
    CASE_WORKFLOW_NAME,
    CASE_WORKFLOW_VERSION,
    WORKFLOW_VERSION_STATE_KEY,
)

CLASSIFIED_CASE_STATE_KEY = "classified_case"

_BRANCH_KEYS = (
    "collect_manufacturing",
    "collect_qc",
    "collect_environmental_monitoring",
    "collect_supply",
)


@node(name="intake_and_bind_evidence")
def intake_and_bind_evidence(node_input: DeviationCaseRequest) -> Event:
    """Classifies the deviation and builds the shared fan-out bundle."""
    classified = classify_intake(node_input.intake)
    attachment = node_input.evidence
    bundle = CaseEvidenceBundle(
        tenant_id=classified.tenant_id,
        case_id=classified.case_id,
        batch_id=classified.batch_id,
        lifecycle_state=classified.lifecycle_state.value,
        bmr=EvidenceIngestRequest(
            tenant_id=classified.tenant_id,
            case_id=classified.case_id,
            batch_id=classified.batch_id,
            source_uri=attachment.source_uri,
            mime_type=attachment.mime_type or "application/pdf",
            content_base64=attachment.content_base64,
            page_count=attachment.page_count,
            spec_version_expected=attachment.spec_version_expected,
        ),
        drive_file_id=attachment.drive_file_id,
    )
    return Event(
        output=bundle.model_dump(mode="json"),
        state={
            CLASSIFIED_CASE_STATE_KEY: classified.model_dump(mode="json"),
            WORKFLOW_VERSION_STATE_KEY: CASE_WORKFLOW_VERSION,
        },
    )


def _parse_branch(key: str, raw: Any) -> BranchOutcome:
    if raw is None:
        department = {
            "collect_manufacturing": Department.MANUFACTURING,
            "collect_qc": Department.QC,
            "collect_environmental_monitoring": Department.ENVIRONMENTAL_MONITORING,
            "collect_supply": Department.SUPPLY,
        }[key]
        return BranchOutcome(
            department=department,
            status=BranchStatus.FAILED,
            error=f"JoinNode missing {key}.",
        )
    return BranchOutcome.model_validate(raw)


@node(name="reconcile_departments")
def reconcile_departments(
    node_input: dict[str, Any],
    classified_case: dict,
) -> CaseComplianceResult:
    """Joins department outputs with the classified case from session state."""
    classified = ClassifiedCase.model_validate(classified_case)
    branches = [_parse_branch(key, node_input.get(key)) for key in _BRANCH_KEYS]

    manufacturing = next(
        (branch for branch in branches if branch.department is Department.MANUFACTURING),
        None,
    )
    if manufacturing is not None and manufacturing.status is BranchStatus.OK:
        if manufacturing.manufacturing_compliance is not None:
            mfg = manufacturing.manufacturing_compliance
            if (
                mfg.tenant_id != classified.tenant_id
                or mfg.case_id != classified.case_id
                or mfg.batch_id != classified.batch_id
            ):
                raise EvidenceIntegrityError(
                    "Manufacturing compliance scope does not match the "
                    "classified case."
                )

    findings = [finding for branch in branches for finding in branch.findings]
    any_failed = any(branch.status is BranchStatus.FAILED for branch in branches)
    mfg_result = (
        manufacturing.manufacturing_compliance
        if manufacturing is not None
        else None
    )
    if any_failed or findings:
        status = ComplianceStatus.INSUFFICIENT_EVIDENCE
    elif mfg_result is not None:
        status = mfg_result.status
    else:
        status = ComplianceStatus.INSUFFICIENT_EVIDENCE

    return CaseComplianceResult(
        classified=classified,
        compliance=mfg_result,
        aggregated_status=status,
        branches=branches,
        findings=findings,
        decision_authority="none",
    )


def build_deviation_case_workflow() -> Workflow:
    """Builds the full deviation-case Workflow through QP confirmation."""
    join_departments = JoinNode(name="join_departments")
    return Workflow(
        name=CASE_WORKFLOW_NAME,
        input_schema=DeviationCaseRequest,
        output_schema=QpReviewedPackage,
        edges=[
            (START, intake_and_bind_evidence),
            (
                intake_and_bind_evidence,
                (
                    collect_manufacturing,
                    collect_qc,
                    collect_environmental_monitoring,
                    collect_supply,
                ),
                join_departments,
                reconcile_departments,
                retrieve_prior_art,
                retrieve_controlled_guidance,
                draft_investigation,
                challenge_investigation,
                gate_investigation,
                await_sterility_incubation,
                evaluate_annex16_release_gate_node,
                confirm_qp,
                record_case_memory,
                file_qms_report,
            ),
        ],
    )
