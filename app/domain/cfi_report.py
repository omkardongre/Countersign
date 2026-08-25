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

"""Deterministic CFI report text.

This module assembles a filed investigation record from the typed CFI
aggregate. It does not select an outcome, interpret a trend, draft a root
cause, or change a QP/release record. The CFI's evidence provenance and
three-outcome closure are reproduced exactly as captured, and every report
states that it is not a batch disposition.
"""

from __future__ import annotations

from app.domain.cfi_models import CfiInvestigation


def build_cfi_report_title(investigation: CfiInvestigation) -> str:
    return (
        f"CFI Investigation Report — {investigation.tenant_id} / "
        f"{investigation.source_case_id} / batch {investigation.batch_id}"
    )


def build_cfi_report_body(investigation: CfiInvestigation) -> str:
    lines: list[str] = []
    lines.append(build_cfi_report_title(investigation))
    lines.append("")
    lines.append("SECTION 1: CFI IDENTITY")
    lines.append(f"CFI ID: {investigation.cfi_id}")
    lines.append(f"Tenant: {investigation.tenant_id}")
    lines.append(f"Source case: {investigation.source_case_id}")
    lines.append(f"Batch: {investigation.batch_id}")
    lines.append(f"Opened at: {investigation.opened_at.isoformat()}")
    lines.append(f"Launch status: {investigation.launch_status.value}")
    lines.append(f"Launch attempts: {investigation.launch_attempts}")
    lines.append(f"Trigger reasons: {', '.join(reason.value for reason in investigation.trigger_reasons)}")
    lines.append(f"Trigger specification: {investigation.trigger_spec_version} ({investigation.trigger_spec_sha256})")
    lines.append("")

    lines.append("SECTION 2: HYPOTHESIS LEDGER")
    if not investigation.hypotheses:
        lines.append("No hypotheses were opened.")
    for hypothesis in investigation.hypotheses:
        lines.append(
            f"{hypothesis.hypothesis_id} [{hypothesis.category.value}] "
            f"{hypothesis.state.value}: {hypothesis.statement}"
        )
        for claim in hypothesis.supporting_claims:
            lines.append(
                f"  Supporting claim: {claim.claim} "
                f"(evidence: {', '.join(claim.evidence_ids)})"
            )
        for claim in hypothesis.refuting_claims:
            lines.append(
                f"  Refuting claim: {claim.claim} "
                f"(evidence: {', '.join(claim.evidence_ids)})"
            )
    lines.append("")

    lines.append("SECTION 3: CAPTURED EVIDENCE AND PROVENANCE")
    if not investigation.evidence:
        lines.append("No evidence was captured from an external tool call.")
    for item in investigation.evidence:
        lines.append(
            f"{item.evidence_id} round {item.round_number} "
            f"[{item.source_tool}] {item.fact}"
        )
        lines.append(f"  Source: {item.source_uri}")
        lines.append(f"  Captured at: {item.captured_at.isoformat()}")
    lines.append("")

    lines.append("SECTION 4: CLOSURE")
    lines.append(f"Outcome: {investigation.outcome.value if investigation.outcome else 'OPEN'}")
    lines.append(f"Closed at: {investigation.closed_at.isoformat() if investigation.closed_at else 'not closed'}")
    lines.append(f"Planning rounds used: {investigation.budget.planning_rounds_used}")
    lines.append(
        f"Tool calls in current round: {investigation.budget.tool_calls_in_current_round}"
    )
    lines.append(f"Budget specification: {investigation.budget.budget_spec_version} ({investigation.budget.budget_spec_sha256})")
    lines.append("")

    lines.append("SECTION 5: SITE REVIEW ARTIFACT")
    if investigation.site_review is None:
        lines.append("Site-review Calendar event was not filed.")
        if investigation.site_review_error:
            lines.append(f"Site-review filing error: {investigation.site_review_error}")
    else:
        lines.append(f"Calendar: {investigation.site_review.calendar_id}")
        lines.append(f"Event ID: {investigation.site_review.event_id}")
        lines.append(f"Event URL: {investigation.site_review.event_url}")
        lines.append(f"Start: {investigation.site_review.start_at.isoformat()}")
        lines.append(f"End: {investigation.site_review.end_at.isoformat()}")
        lines.append(
            f"Attendees: {', '.join(investigation.site_review.attendees) or 'none configured'}"
        )
    lines.append("")

    lines.append("SECTION 6: FILED DOCUMENT")
    lines.append(
        "This deterministic report body is submitted through the tenant-locked "
        "cfi_report_write boundary. The resulting document id and URL are "
        "stored on the durable CFI aggregate rather than repeated here, so "
        "retries submit byte-identical content."
    )
    lines.append("")

    lines.append(
        "This is a Cross-Functional Investigation record only. It is not a "
        "batch disposition, release decision, QP confirmation, or certified "
        "root-cause determination. Shared-batch and trend results are "
        "associations for human review. Human interview text is untrusted "
        "evidence. decision_authority: none throughout."
    )
    return "\n".join(lines)
