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

"""Joined deviation case graph: intake → evidence extraction → compliance.

First runnable slice of the fleet spine in ``final_reseach/05_architecture.md``
section 5. ADK 2.x graph patterns used here (from ``adk-docs/docs/graphs`` and
``agents-cli`` workflow cheatsheet):

* Outer ``Workflow`` with ``input_schema`` / ``output_schema`` so START parses
  a ``DeviationCaseRequest`` instead of raw ``Content``.
* Evidence slice composed as a nested ``Workflow`` node, not copied edges
  (``Workflow`` is a ``BaseNode``; ``(predecessor, inner_workflow)`` is the
  documented composition).
* Classification travels in ``Event.state``, not on the evidence payload.
  Official data-handling guidance: ``output`` is the successor's typed input;
  ``state`` is for values later nodes need that must not widen an existing
  contract. Bytes stay in the artifact service.

    START -> intake_and_bind_evidence
          -> evidence_extraction (nested workflow)
          -> attach_classified_case

Investigation, Challenger, durable wait and QP ``RequestInput`` attach later.
Parallel MES/LIMS/EM/CoA branches attach as sibling ingest paths into a
``JoinNode`` before compliance.
"""

from __future__ import annotations

from google.adk.events.event import Event
from google.adk.workflow import START, Workflow, node

from app.domain.case_models import CaseComplianceResult, DeviationCaseRequest
from app.domain.compliance_models import ComplianceResult
from app.domain.evidence_graph import build_evidence_extraction_workflow
from app.domain.evidence_models import EvidenceIngestRequest
from app.domain.ingest import EvidenceIntegrityError
from app.domain.intake import classify_intake
from app.domain.models import ClassifiedCase

CASE_WORKFLOW_NAME = "deviation_case"
CLASSIFIED_CASE_STATE_KEY = "classified_case"


@node(name="intake_and_bind_evidence")
def intake_and_bind_evidence(node_input: DeviationCaseRequest) -> Event:
    """Classifies the deviation and binds evidence to that case's scope.

    Args:
        node_input: Reporter intake plus one retrieved evidence attachment.

    Returns:
        An ``Event`` whose ``output`` is the ingest request (tenant/case/batch
        stamped from the classified case) and whose ``state`` holds the
        classified case for the terminal join.
    """
    classified = classify_intake(node_input.intake)
    attachment = node_input.evidence
    ingest = EvidenceIngestRequest(
        tenant_id=classified.tenant_id,
        case_id=classified.case_id,
        batch_id=classified.batch_id,
        source_uri=attachment.source_uri,
        mime_type=attachment.mime_type,
        content_base64=attachment.content_base64,
        page_count=attachment.page_count,
        spec_version_expected=attachment.spec_version_expected,
    )
    return Event(
        output=ingest.model_dump(mode="json"),
        state={CLASSIFIED_CASE_STATE_KEY: classified.model_dump(mode="json")},
    )


@node(name="attach_classified_case")
def attach_classified_case(
    node_input: ComplianceResult,
    classified_case: dict,
) -> CaseComplianceResult:
    """Rejoins classification (from session state) with first-page compliance.

    ``classified_case`` is bound from ``ctx.state`` by parameter name, per
    ADK FunctionNode resolution (not from the evidence payload).

    Args:
        node_input: Compliance output of the nested evidence workflow.
        classified_case: Classified case written at intake.

    Returns:
        A ``CaseComplianceResult`` whose case and evidence share tenant,
        case id and batch id.

    Raises:
        EvidenceIntegrityError: If classification disagrees with evidence
            scope. A mismatch is a graph bug, not a negotiable value.
    """
    classified = ClassifiedCase.model_validate(classified_case)
    if (
        classified.tenant_id != node_input.tenant_id
        or classified.case_id != node_input.case_id
        or classified.batch_id != node_input.batch_id
    ):
        raise EvidenceIntegrityError(
            "Classified case scope does not match the compliance result "
            f"(case {classified.case_id}/{classified.tenant_id}/"
            f"{classified.batch_id} vs evidence {node_input.case_id}/"
            f"{node_input.tenant_id}/{node_input.batch_id})."
        )
    return CaseComplianceResult(
        classified=classified,
        compliance=node_input,
        decision_authority="none",
    )


def build_deviation_case_workflow() -> Workflow:
    """Builds the joined intake → nested evidence graph → compliance result.

    Returns:
        A ``Workflow`` taking ``DeviationCaseRequest`` at START and producing
        ``CaseComplianceResult``.
    """
    evidence_workflow = build_evidence_extraction_workflow()
    return Workflow(
        name=CASE_WORKFLOW_NAME,
        input_schema=DeviationCaseRequest,
        output_schema=CaseComplianceResult,
        edges=[
            (START, intake_and_bind_evidence),
            (intake_and_bind_evidence, evidence_workflow),
            (evidence_workflow, attach_classified_case),
        ],
    )
