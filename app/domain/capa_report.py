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

"""Builds the deterministic content of the ERPNext Non Conformance and
Quality Action records that ``capa_write_agent`` creates after QP action.

This is prose assembly, not a decision: every line here is read off a typed
field the workflow already produced (``QpReviewedPackage``). Nothing here
computes a finding, a root cause, or a disposition. The text states
``decision_authority: none`` explicitly, mirroring every domain model it
draws from.

Kept separate from ``capa_write_agent.py``/the MCP tool so the text this
node produces is unit-testable without any ERPNext API or MCP transport.
"""

from __future__ import annotations

from app.domain.investigation_models import RootCauseHypothesis
from app.domain.qp_models import QpReviewedPackage


def build_nonconformance_subject(reviewed: QpReviewedPackage) -> str:
    """Short subject for the ERPNext Non Conformance ``subject`` field
    (doctype reqd, max 256 chars)."""
    classified = reviewed.release_package.investigation.case.classified
    confirmation = reviewed.qp_confirmation
    return (
        f"Case {classified.case_id} (batch {classified.batch_id}, "
        f"{classified.severity.value}): QP {confirmation.outcome.value}. "
        f"{classified.rationale}"
    )[:256]


def build_nonconformance_details(reviewed: QpReviewedPackage) -> str:
    """Full details for the ERPNext Non Conformance ``details`` field
    (Text Editor). Assembled entirely from the stored case aggregate — the
    model never drafts this content.
    """
    package = reviewed.release_package
    investigation = package.investigation
    case = investigation.case
    classified = case.classified
    confirmation = reviewed.qp_confirmation

    lines: list[str] = []
    lines.append(f"Countersign deviation case {classified.case_id}")
    lines.append(f"Tenant: {classified.tenant_id}")
    lines.append(f"Batch: {classified.batch_id}")
    lines.append(f"Product: {classified.product_name}")
    lines.append(f"Severity: {classified.severity.value}")
    lines.append(f"Classification rationale: {classified.rationale}")
    lines.append("")
    lines.append("Findings:")
    for finding in case.findings:
        lines.append(
            f"  [{finding.finding_type.value}] {finding.label}: "
            f"{finding.description}"
        )
    if not case.findings:
        lines.append("  No compliance findings were raised.")
    lines.append("")
    lines.append("QP decision:")
    lines.append(f"  Action: {confirmation.action.value}")
    lines.append(f"  Outcome: {confirmation.outcome.value}")
    lines.append(f"  Rationale: {confirmation.rationale}")
    lines.append("")
    lines.append(
        "This Non Conformance was created automatically by Countersign "
        "after the QP recorded their decision. It is not a batch "
        "disposition. decision_authority: none."
    )
    return "\n".join(lines)


def build_quality_action_problem(reviewed: QpReviewedPackage) -> str:
    """Problem statement for the Quality Action ``resolutions`` child table
    row ``problem`` field (Long Text)."""
    classified = reviewed.release_package.investigation.case.classified
    confirmation = reviewed.qp_confirmation
    lines: list[str] = []
    lines.append(f"Case {classified.case_id} (batch {classified.batch_id})")
    lines.append(f"Severity: {classified.severity.value}")
    lines.append(f"Classification rationale: {classified.rationale}")
    lines.append(f"QP decision: {confirmation.outcome.value}")
    lines.append(f"QP rationale: {confirmation.rationale}")
    return "\n".join(lines)


def build_quality_action_resolution(
    reviewed: QpReviewedPackage,
    *,
    action_type: str,
) -> str:
    """Resolution text for the Quality Action ``resolutions`` child table
    row ``resolution`` field (Text Editor).

    ``action_type`` is the deterministic ``CapaActionType`` value chosen by
    ``capa_write_agent``'s caller (corrective for the immediate fix,
    preventive for systemic prevention). The resolution text is assembled
    from the investigation draft's leading hypothesis and the QP rationale —
    never from model-generated prose.
    """
    package = reviewed.release_package
    investigation = package.investigation
    case = investigation.case
    classified = case.classified
    confirmation = reviewed.qp_confirmation

    lines: list[str] = []
    lines.append(f"Countersign {action_type} action for case {classified.case_id}")
    lines.append(f"Tenant: {classified.tenant_id}")
    lines.append(f"Batch: {classified.batch_id}")
    lines.append("")

    draft = investigation.draft
    if draft and draft.hypotheses:
        leading: RootCauseHypothesis = draft.hypotheses[0]
        lines.append(
            f"Leading root-cause hypothesis [{leading.category.value}]: "
            f"{leading.statement}"
        )
        lines.append("")
        lines.append("Supporting evidence:")
        for claim in leading.supporting_claims:
            sources = "; ".join(claim.source_uris)
            lines.append(f"  - {claim.claim} (sources: {sources})")
    else:
        lines.append("No root-cause hypothesis was drafted for this case.")

    lines.append("")
    lines.append(f"QP rationale: {confirmation.rationale}")
    lines.append("")

    if action_type == "Corrective":
        lines.append(
            "Corrective action: address the immediate deviation on this "
            "batch. Execute the QP's recorded decision and any site-review "
            "actions scheduled by the CFI closure."
        )
    else:
        lines.append(
            "Preventive action: investigate whether the root-cause "
            "conditions could affect other batches sharing the same "
            "material lot, equipment, or process line. Implement "
            "controls to prevent recurrence."
        )

    lines.append("")
    lines.append(
        "This Quality Action was created automatically by Countersign "
        "after the QP recorded their decision. It is not a batch "
        "disposition. decision_authority: none."
    )
    return "\n".join(lines)
