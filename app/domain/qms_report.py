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

"""Builds the deterministic content of the investigation report + Annex 16
package that ``qa_qms_write_agent`` files to Google Docs after QP action.

This is prose assembly, not a decision: every line here is read off a typed
field the workflow already produced (``QpReviewedPackage``). Nothing here
computes a finding, a root cause, or a disposition. The report states
``decision_authority: none`` explicitly, mirroring every domain model it
draws from, because a filed artifact making an implicit claim of authority
just by looking official is exactly the failure mode this project's honesty
posture (see ``final_reseach/05_architecture.md`` section 0) rejects.

Kept separate from ``qms_report_agent.py``/the MCP tool so the text this
node produces is unit-testable without any Docs API or MCP transport.
"""

from __future__ import annotations

from app.domain.department_models import BranchOutcome
from app.domain.investigation_models import RootCauseHypothesis
from app.domain.qp_models import QpReviewedPackage
from app.domain.release_models import ChecklistItemResult

_STATUS_LABELS = {
    "pass": "PASS",
    "fail": "FAIL",
    "not_evaluated": "NOT EVALUATED",
}


def _branch_section(branch: BranchOutcome) -> list[str]:
    lines = [
        f"{branch.department.value.replace('_', ' ').title()} — "
        f"{branch.status.value.upper()}"
    ]
    if branch.error:
        lines.append(f"  Error: {branch.error}")
    for uri in branch.source_uris:
        lines.append(f"  Source: {uri}")
    for finding in branch.findings:
        lines.append(
            f"  Finding [{finding.finding_type.value}] {finding.label}: "
            f"{finding.description}"
        )
    for advisory in branch.advisory_findings:
        lines.append(
            f"  Advisory-only [{advisory.source.value}] {advisory.title}: "
            f"{advisory.description} (does not affect release gating)"
        )
    if branch.regulatory_screening is not None:
        lines.append(
            "  Regulatory screening is public advisory context only; "
            "it is not supplier clearance and is not used by the release gate."
        )
    if (
        not branch.error
        and not branch.findings
        and not branch.advisory_findings
        and branch.status.value == "ok"
    ):
        lines.append("  No findings on this branch.")
    return lines


def _hypothesis_section(hypothesis: RootCauseHypothesis, index: int) -> list[str]:
    lines = [
        f"Hypothesis {index} [{hypothesis.category.value}] "
        f"(confidence {hypothesis.confidence:.2f}): {hypothesis.statement}"
    ]
    for claim in hypothesis.supporting_claims:
        sources = "; ".join(claim.source_uris)
        lines.append(f"  Citation: {claim.claim} — sources: {sources}")
    return lines


def _checklist_section(item: ChecklistItemResult) -> list[str]:
    status_label = _STATUS_LABELS.get(item.status.value, item.status.value.upper())
    lines = [f"{item.annex_ref} {item.title}: {status_label} — {item.reason}"]
    for uri in item.evidence_uris:
        lines.append(f"  Evidence: {uri}")
    return lines


def build_report_title(reviewed: QpReviewedPackage) -> str:
    classified = reviewed.release_package.investigation.case.classified
    return (
        f"Deviation Investigation Report — {classified.tenant_id} / "
        f"{classified.case_id} / batch {classified.batch_id}"
    )


def build_report_body(reviewed: QpReviewedPackage) -> str:
    """Plain-text report body. Line breaks are structural: the caller
    inserts this unchanged via one Docs ``InsertTextRequest``, then applies
    paragraph styling by scanning for the section headers this function
    always emits verbatim.
    """
    package = reviewed.release_package
    investigation = package.investigation
    case = investigation.case
    classified = case.classified
    confirmation = reviewed.qp_confirmation

    lines: list[str] = []
    lines.append(build_report_title(reviewed))
    lines.append("")
    lines.append("SECTION 1: CASE IDENTITY")
    lines.append(f"Tenant: {classified.tenant_id}")
    lines.append(f"Case ID: {classified.case_id}")
    lines.append(f"Batch: {classified.batch_id}")
    lines.append(f"Product: {classified.product_name}")
    lines.append(f"Document type: {classified.doc_type.value}")
    lines.append(f"Severity: {classified.severity.value}")
    lines.append(f"Classification rationale: {classified.rationale}")
    lines.append(f"Classified at: {classified.classified_at.isoformat()}")
    lines.append("")

    lines.append("SECTION 2: DEPARTMENT EVIDENCE")
    for branch in case.branches:
        lines.extend(_branch_section(branch))
        lines.append("")

    lines.append("SECTION 3: INVESTIGATION")
    draft = investigation.draft
    if investigation.gate.passed is False and draft is None:
        lines.append(
            "Investigation was not drafted for this case "
            f"(skip reason: {getattr(package, 'skip_reason', None) or 'n/a'})."
        )
    elif draft is None:
        lines.append("No investigation draft is attached to this case.")
    else:
        if draft.hypotheses:
            for index, hypothesis in enumerate(draft.hypotheses, start=1):
                lines.extend(_hypothesis_section(hypothesis, index))
        else:
            lines.append("No root-cause hypotheses were drafted.")
        if draft.product_impact is not None:
            lines.append("")
            lines.append(
                "Draft product impact statement (not a safety determination): "
                f"{draft.product_impact.statement}"
            )
            lines.append(
                "Patient safety touch flagged: "
                f"{draft.product_impact.patient_safety_touch}"
            )
        if draft.open_questions:
            lines.append("")
            lines.append("Open questions:")
            for question in draft.open_questions:
                lines.append(f"  - {question}")
    lines.append("")
    lines.append(
        f"Citation gate passed: {investigation.gate.passed}"
        + (
            f" (blocked: {'; '.join(investigation.gate.blocked_reasons)})"
            if investigation.gate.blocked_reasons
            else ""
        )
    )
    lines.append("")

    lines.append("SECTION 3A: CONTROLLED SOP AND PUBLIC-GUIDANCE CITATIONS")
    if not case.controlled_guidance:
        lines.append("No controlled SOP or public-guidance citation was retrieved.")
    else:
        for citation in case.controlled_guidance:
            status = "in force" if citation.in_force else "NOT in force"
            lines.append(
                f"[{citation.corpus.value}] {citation.doc_id} v{citation.version} "
                f"({status}) — {citation.title}"
            )
            lines.append(f"  Source: {citation.source_uri}")
    lines.append("")

    challenger = investigation.challenger
    lines.append("SECTION 4: CHALLENGER REVIEW")
    if challenger is None or not challenger.attacks:
        lines.append("No challenger attacks were raised on this draft.")
    else:
        for attack in challenger.attacks:
            lines.append(f"[{attack.attack_type.value}] {attack.target}: {attack.detail}")
    lines.append("")

    lines.append("SECTION 5: ANNEX 16 SECTION 1.7 CONFIRMATION CHECKLIST")
    gate = package.release_gate
    lines.append(f"Checklist spec: {gate.spec_id} v{gate.spec_version} ({gate.spec_sha256[:12]}...)")
    lines.append(f"Gate status: {gate.status.value}")
    for item in gate.items:
        lines.extend(_checklist_section(item))
    if gate.blocked_reasons:
        lines.append("Blocked reasons:")
        for reason in gate.blocked_reasons:
            lines.append(f"  - {reason}")
    lines.append("")

    lines.append("SECTION 6: QP DECISION RECORD")
    lines.append(f"Action: {confirmation.action.value}")
    lines.append(f"Outcome: {confirmation.outcome.value}")
    lines.append(f"QP name: {confirmation.qp_name}")
    lines.append(f"Rationale: {confirmation.rationale}")
    lines.append(f"Gate status at decision: {confirmation.gate_status.value}")
    lines.append(f"Recorded at: {confirmation.recorded_at.isoformat()}")
    if confirmation.rejection_reason:
        lines.append(f"Rejection reason: {confirmation.rejection_reason}")
    lines.append("")

    lines.append(
        "This document is a CDMO investigation record and Annex 16 section "
        "1.7 confirmation pack. It is not a Marketing Authorisation Holder "
        "certification and does not itself release a batch. "
        "decision_authority: none throughout."
    )

    return "\n".join(lines)
