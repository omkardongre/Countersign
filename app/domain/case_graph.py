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

"""Deviation case graph: intake → departments → investigation → challenger → gate.

ADK fan-out/fan-in (``workflows/fan_out_fan_in``): after classify, three
department collectors run in parallel and a ``JoinNode`` waits for all of
them. Each collector always emits ``Event.output`` so a failed branch cannot
stall the join (ADK JoinNode caution).

    START -> intake_and_bind_evidence
          -> (collect_manufacturing, collect_qc, collect_supply)
          -> join_departments
          -> reconcile_departments
          -> draft_investigation
          -> challenge_investigation
          -> gate_investigation

Manufacturing still runs the BMR transcriber. QC and supply are deterministic
lookups over connector-shaped LIMS / EM / CoA records. Investigation and
challenger are single-turn LLMs with no tools; the citation gate is code.
"""

from __future__ import annotations

from typing import Any

from google.adk.events.event import Event
from google.adk.workflow import START, JoinNode, Workflow, node

from app.domain.branches import (
    collect_manufacturing,
    collect_qc,
    collect_supply,
)
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
from app.domain.ingest import EvidenceIntegrityError
from app.domain.intake import classify_intake
from app.domain.investigation import draft_investigation
from app.domain.investigation_gate import gate_investigation
from app.domain.investigation_models import InvestigationCaseResult
from app.domain.models import ClassifiedCase

CASE_WORKFLOW_NAME = "deviation_case"
CLASSIFIED_CASE_STATE_KEY = "classified_case"

_BRANCH_KEYS = (
    "collect_manufacturing",
    "collect_qc",
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
        bmr=EvidenceIngestRequest(
            tenant_id=classified.tenant_id,
            case_id=classified.case_id,
            batch_id=classified.batch_id,
            source_uri=attachment.source_uri,
            mime_type=attachment.mime_type,
            content_base64=attachment.content_base64,
            page_count=attachment.page_count,
            spec_version_expected=attachment.spec_version_expected,
        ),
    )
    return Event(
        output=bundle.model_dump(mode="json"),
        state={CLASSIFIED_CASE_STATE_KEY: classified.model_dump(mode="json")},
    )


def _parse_branch(key: str, raw: Any) -> BranchOutcome:
    if raw is None:
        department = {
            "collect_manufacturing": Department.MANUFACTURING,
            "collect_qc": Department.QC,
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
    """Builds the full deviation-case Workflow through the citation gate."""
    join_departments = JoinNode(name="join_departments")
    return Workflow(
        name=CASE_WORKFLOW_NAME,
        input_schema=DeviationCaseRequest,
        output_schema=InvestigationCaseResult,
        edges=[
            (START, intake_and_bind_evidence),
            (
                intake_and_bind_evidence,
                (collect_manufacturing, collect_qc, collect_supply),
                join_departments,
                reconcile_departments,
                draft_investigation,
                challenge_investigation,
                gate_investigation,
            ),
        ],
    )
