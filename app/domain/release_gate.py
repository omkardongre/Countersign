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

"""Deterministic Annex 16 section 1.7 confirmation checklist.

The QP still certifies personally. This node only records, per checklist item,
whether the joined case currently evidences that item. It never releases a batch.
"""

from __future__ import annotations

import functools
import hashlib
import json
import pathlib
from collections.abc import Callable

from google.adk.workflow import node
from pydantic import BaseModel, Field

from app.domain.case_models import CaseComplianceResult
from app.domain.compliance_models import ComplianceFindingType
from app.domain.department_models import BranchOutcome, BranchStatus, Department
from app.domain.investigation_models import InvestigationCaseResult
from app.domain.master_data import RootCauseCategory
from app.domain.release_models import (
    CaseReleasePackage,
    ChecklistItemResult,
    ChecklistItemStatus,
    ReleaseGateResult,
    ReleaseGateStatus,
)

_CHECKLIST_PATH = (
    pathlib.Path(__file__).parent / "master_data" / "annex16_checklist_v1.json"
)

_Evaluator = Callable[
    [InvestigationCaseResult, "Annex16ChecklistItem"], ChecklistItemResult
]


class Annex16ChecklistItem(BaseModel):
    annex_ref: str
    title: str
    evaluator: str
    required_for_confirmation: bool = False


class Annex16Checklist(BaseModel):
    spec_id: str
    spec_version: str
    spec_sha256: str
    items: list[Annex16ChecklistItem] = Field(default_factory=list)


@functools.cache
def load_annex16_checklist() -> Annex16Checklist:
    raw = _CHECKLIST_PATH.read_bytes()
    payload = json.loads(raw)
    return Annex16Checklist(
        spec_id=payload["spec_id"],
        spec_version=payload["spec_version"],
        spec_sha256=hashlib.sha256(raw).hexdigest(),
        items=[Annex16ChecklistItem.model_validate(item) for item in payload["items"]],
    )


def _branch(case: CaseComplianceResult, department: Department) -> BranchOutcome | None:
    return next(
        (branch for branch in case.branches if branch.department is department),
        None,
    )


def _uris(*branches: BranchOutcome | None) -> list[str]:
    uris: list[str] = []
    for branch in branches:
        if branch is None:
            continue
        uris.extend(branch.source_uris)
    return uris


def _result(
    spec_item: Annex16ChecklistItem,
    *,
    status: ChecklistItemStatus,
    reason: str,
    evidence_uris: list[str] | None = None,
) -> ChecklistItemResult:
    return ChecklistItemResult(
        annex_ref=spec_item.annex_ref,
        title=spec_item.title,
        status=status,
        evidence_uris=evidence_uris or [],
        reason=reason,
        required_for_confirmation=spec_item.required_for_confirmation,
    )


def _eval_records_complete(
    package: InvestigationCaseResult, spec_item: Annex16ChecklistItem
) -> ChecklistItemResult:
    manufacturing = _branch(package.case, Department.MANUFACTURING)
    uris = _uris(manufacturing)
    if manufacturing is None or manufacturing.status is BranchStatus.FAILED:
        return _result(
            spec_item,
            status=ChecklistItemStatus.FAIL,
            reason="Manufacturing record was not collected.",
            evidence_uris=uris,
        )
    completeness = [
        finding
        for finding in manufacturing.findings
        if finding.finding_type is ComplianceFindingType.COMPLETENESS
    ]
    if completeness:
        return _result(
            spec_item,
            status=ChecklistItemStatus.FAIL,
            reason="Completeness findings remain on the manufacturing record.",
            evidence_uris=uris,
        )
    return _result(
        spec_item,
        status=ChecklistItemStatus.PASS,
        reason="Manufacturing branch collected; no completeness findings.",
        evidence_uris=uris,
    )


def _eval_finished_product_qc(
    package: InvestigationCaseResult, spec_item: Annex16ChecklistItem
) -> ChecklistItemResult:
    qc = _branch(package.case, Department.QC)
    uris = _uris(qc)
    if qc is None or qc.status is BranchStatus.FAILED:
        return _result(
            spec_item,
            status=ChecklistItemStatus.FAIL,
            reason="QC branch was not collected.",
            evidence_uris=uris,
        )
    lims_oos = [
        finding
        for finding in qc.findings
        if finding.finding_type is ComplianceFindingType.OUT_OF_SPECIFICATION
        and finding.root_cause_category is RootCauseCategory.MEASUREMENT
    ]
    if lims_oos:
        return _result(
            spec_item,
            status=ChecklistItemStatus.FAIL,
            reason="Finished-product QC results are outside specification.",
            evidence_uris=uris,
        )
    return _result(
        spec_item,
        status=ChecklistItemStatus.PASS,
        reason="QC branch collected; no finished-product OOS findings.",
        evidence_uris=uris,
    )


def _eval_materials_gating(
    package: InvestigationCaseResult, spec_item: Annex16ChecklistItem
) -> ChecklistItemResult:
    supply = _branch(package.case, Department.SUPPLY)
    uris = _uris(supply)
    if supply is None or supply.status is BranchStatus.FAILED:
        return _result(
            spec_item,
            status=ChecklistItemStatus.FAIL,
            reason="Supplier CoA branch was not collected.",
            evidence_uris=uris,
        )
    if supply.findings:
        return _result(
            spec_item,
            status=ChecklistItemStatus.FAIL,
            reason="Supplier CoA gating findings remain on the case.",
            evidence_uris=uris,
        )
    return _result(
        spec_item,
        status=ChecklistItemStatus.PASS,
        reason=(
            "Supplier CoA collected; signature, lot, retest and "
            "certificate checks passed."
        ),
        evidence_uris=uris,
    )


def _eval_investigations_complete(
    package: InvestigationCaseResult, spec_item: Annex16ChecklistItem
) -> ChecklistItemResult:
    return _result(
        spec_item,
        status=ChecklistItemStatus.FAIL,
        reason=(
            "Batch investigation is open "
            f"(lifecycle {package.case.classified.lifecycle_state.value}); "
            "Annex 16 1.7.16 requires investigations complete enough to support "
            "certification. This node does not close or certify the investigation."
        ),
        evidence_uris=_uris(*package.case.branches),
    )


def _eval_open_batch_issues(
    package: InvestigationCaseResult, spec_item: Annex16ChecklistItem
) -> ChecklistItemResult:
    return _result(
        spec_item,
        status=ChecklistItemStatus.FAIL,
        reason=(
            f"Case {package.case.classified.case_id} is an open deviation "
            f"investigation for batch {package.case.classified.batch_id}. "
            "Whether that negates certification is a QP judgement, not a "
            "code pass."
        ),
        evidence_uris=_uris(*package.case.branches),
    )


def _eval_technical_agreement(
    package: InvestigationCaseResult, spec_item: Annex16ChecklistItem
) -> ChecklistItemResult:
    return _result(
        spec_item,
        status=ChecklistItemStatus.NOT_EVALUATED,
        reason=(
            f"Tenant {package.case.classified.tenant_id} is on the case; "
            "Technical Quality Agreement currency is not verified until the "
            "TQA authorization extension is attached."
        ),
    )


_EVALUATORS: dict[str, _Evaluator] = {
    "records_complete": _eval_records_complete,
    "finished_product_qc": _eval_finished_product_qc,
    "materials_gating": _eval_materials_gating,
    "investigations_complete": _eval_investigations_complete,
    "open_batch_issues": _eval_open_batch_issues,
    "technical_agreement": _eval_technical_agreement,
}


def evaluate_annex16_release_gate(
    package: InvestigationCaseResult,
) -> ReleaseGateResult:
    checklist = load_annex16_checklist()
    items: list[ChecklistItemResult] = []
    for spec_item in checklist.items:
        if spec_item.evaluator == "none":
            items.append(
                _result(
                    spec_item,
                    status=ChecklistItemStatus.NOT_EVALUATED,
                    reason=(
                        "No evaluator is bound for this item in the approved checklist."
                    ),
                )
            )
            continue
        evaluator = _EVALUATORS.get(spec_item.evaluator)
        if evaluator is None:
            items.append(
                _result(
                    spec_item,
                    status=ChecklistItemStatus.FAIL,
                    reason=(
                        f"Unknown evaluator {spec_item.evaluator!r}; "
                        "checklist evaluation fails closed."
                    ),
                )
            )
            continue
        items.append(evaluator(package, spec_item))

    blocked: list[str] = []
    for item in items:
        if item.status is ChecklistItemStatus.FAIL:
            blocked.append(f"{item.annex_ref}: {item.reason}")
        elif (
            item.required_for_confirmation
            and item.status is ChecklistItemStatus.NOT_EVALUATED
        ):
            blocked.append(f"{item.annex_ref}: required item was not evaluated.")

    status = (
        ReleaseGateStatus.READY_FOR_QP_REVIEW
        if not blocked
        else ReleaseGateStatus.NOT_READY
    )
    return ReleaseGateResult(
        status=status,
        spec_id=checklist.spec_id,
        spec_version=checklist.spec_version,
        spec_sha256=checklist.spec_sha256,
        items=items,
        blocked_reasons=blocked,
        decision_authority="none",
    )


@node(name="evaluate_annex16_release_gate")
def evaluate_annex16_release_gate_node(
    node_input: InvestigationCaseResult,
) -> CaseReleasePackage:
    investigation = (
        node_input
        if isinstance(node_input, InvestigationCaseResult)
        else InvestigationCaseResult.model_validate(node_input)
    )
    return CaseReleasePackage(
        investigation=investigation,
        release_gate=evaluate_annex16_release_gate(investigation),
        decision_authority="none",
    )
