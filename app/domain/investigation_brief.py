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

"""Compact, citation-constrained brief for investigation / challenger agents."""

from __future__ import annotations

from app.domain.case_models import CaseComplianceResult


def allowed_source_uris(case: CaseComplianceResult) -> set[str]:
    uris: set[str] = set(case.classified.evidence_refs)
    for branch in case.branches:
        uris.update(branch.source_uris)
        for finding in branch.findings:
            if finding.anchor is not None:
                uris.add(finding.anchor.source_uri)
    if case.compliance is not None:
        for finding in case.compliance.findings:
            if finding.anchor is not None:
                uris.add(finding.anchor.source_uri)
    for fact in case.prior_art:
        if not fact.redacted:
            uris.add(fact.source_uri)
    # Doc 12 T1-5: only in-force (approved, not superseded) controlled
    # SOP/public-guidance citations are citeable. A stale or unapproved
    # guidance citation is deliberately excluded from the catalogue rather
    # than left in and merely unable to satisfy the mandatory-SOP check —
    # citing a withdrawn procedure as if it were current is itself a
    # citation-gate failure, not a lesser one.
    for citation in case.controlled_guidance:
        if citation.in_force:
            uris.add(citation.source_uri)
    return {uri for uri in uris if uri}


def allowed_finding_ids(case: CaseComplianceResult) -> set[str]:
    ids: set[str] = set()
    for finding in case.findings:
        ids.add(finding.finding_id)
    for branch in case.branches:
        for finding in branch.findings:
            ids.add(finding.finding_id)
    if case.compliance is not None:
        for finding in case.compliance.findings:
            ids.add(finding.finding_id)
    return ids


def build_investigation_brief(case: CaseComplianceResult) -> dict:
    """JSON the investigation agent is allowed to cite from.

    Acceptance ranges, model weights, and any system the agent cannot reach
    are omitted. The allowed URI and finding-id catalogues are the citation
    universe the deterministic gate will enforce.
    """
    classified = case.classified
    prior_art = [
        {
            "fact_id": fact.fact_id,
            "fact": fact.fact,
            "source_uri": fact.source_uri,
            "confidence": fact.confidence,
            "root_cause_category": (
                fact.root_cause_category.value
                if fact.root_cause_category is not None
                else None
            ),
            "similarity_score": fact.similarity_score,
        }
        for fact in case.prior_art
        if not fact.redacted
    ]
    controlled_guidance = [
        {
            "doc_id": citation.doc_id,
            "corpus": citation.corpus.value,
            "title": citation.title,
            "version": citation.version,
            "source_uri": citation.source_uri,
            "text": citation.text,
            "similarity_score": citation.similarity_score,
        }
        for citation in case.controlled_guidance
        if citation.in_force
    ]
    findings = []
    for finding in case.findings:
        findings.append(
            {
                "finding_id": finding.finding_id,
                "finding_type": finding.finding_type.value,
                "field_name": finding.field_name,
                "label": finding.label,
                "root_cause_category": finding.root_cause_category.value,
                "description": finding.description,
                "source_uri": (
                    finding.anchor.source_uri if finding.anchor is not None else None
                ),
            }
        )
    return {
        "tenant_id": classified.tenant_id,
        "case_id": classified.case_id,
        "batch_id": classified.batch_id,
        "product_name": classified.product_name,
        "severity": classified.severity.value,
        "aggregated_status": case.aggregated_status.value,
        "decision_authority": "none",
        "allowed_source_uris": sorted(allowed_source_uris(case)),
        "allowed_finding_ids": sorted(allowed_finding_ids(case)),
        "branches": [
            {
                "department": branch.department.value,
                "status": branch.status.value,
                "error": branch.error,
                "source_uris": list(branch.source_uris),
            }
            for branch in case.branches
        ],
        "findings": findings,
        "classification_rationale": classified.rationale,
        "prior_art": prior_art,
        "prior_art_embedded": case.prior_art_embedded,
        "controlled_guidance": controlled_guidance,
    }
