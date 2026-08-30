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

"""Deterministic QC and supply checks over connector-shaped records."""

from __future__ import annotations

import datetime
import hashlib

from app.domain.compliance_models import (
    ComplianceFinding,
    ComplianceFindingType,
    OosPhase,
)
from app.domain.department_models import CoaSnapshot, EmSnapshot, LimsSnapshot
from app.domain.evidence_models import SourceAnchor
from app.domain.master_data import RootCauseCategory


def _anchor(
    source_uri: str, identity: str, *, artifact_sha256: str | None = None
) -> SourceAnchor:
    digest = artifact_sha256 or hashlib.sha256(identity.encode()).hexdigest()
    return SourceAnchor(
        artifact_filename=identity,
        artifact_version=1,
        artifact_sha256=digest,
        source_uri=source_uri,
        page_number=1,
        transcribed_context="",
    )


def check_lims(snapshot: LimsSnapshot) -> list[ComplianceFinding]:
    """Flags assay (and other) results outside the LIMS spec window."""
    findings: list[ComplianceFinding] = []
    for row in snapshot.rows:
        if row.result_value < row.spec_min or row.result_value > row.spec_max:
            findings.append(
                ComplianceFinding(
                    finding_type=ComplianceFindingType.OUT_OF_SPECIFICATION,
                    field_name=row.test_code,
                    label=row.label,
                    root_cause_category=RootCauseCategory.MEASUREMENT,
                    description=(
                        f"LIMS {row.test_code} sample {row.sample_id} is "
                        f"{row.result_value} {row.unit}, outside "
                        f"{row.spec_min}-{row.spec_max} {row.unit}."
                    ),
                    anchor=_anchor(
                        snapshot.source_uri,
                        f"lims:{row.sample_id}:{row.test_code}",
                    ),
                    observed_value=f"{row.result_value} {row.unit}",
                    expected_value=f"{row.spec_min}-{row.spec_max} {row.unit}",
                    oos_phase=OosPhase.PHASE_I_LABORATORY,
                )
            )
    return findings


def check_em(snapshot: EmSnapshot) -> list[ComplianceFinding]:
    """Flags environmental readings at or above the action limit."""
    findings: list[ComplianceFinding] = []
    for row in snapshot.rows:
        if row.value >= row.action_limit:
            findings.append(
                ComplianceFinding(
                    finding_type=ComplianceFindingType.OUT_OF_SPECIFICATION,
                    field_name=row.parameter,
                    label=row.label,
                    root_cause_category=RootCauseCategory.ENVIRONMENT,
                    description=(
                        f"EM {row.parameter} at {row.location_id} is "
                        f"{row.value} {row.unit}, at or above action limit "
                        f"{row.action_limit} {row.unit}."
                    ),
                    anchor=_anchor(
                        snapshot.source_uri,
                        f"em:{row.location_id}:{row.parameter}:{row.recorded_at.isoformat()}",
                    ),
                    observed_value=f"{row.value} {row.unit}",
                    expected_value=f"< {row.action_limit} {row.unit}",
                    oos_phase=OosPhase.PHASE_I_LABORATORY,
                )
            )
    return findings


def check_coa(
    snapshot: CoaSnapshot,
    *,
    today: datetime.date | None = None,
) -> list[ComplianceFinding]:
    """Supplier gating: signature, lot match, retest date, recorded GMP expiry."""
    findings: list[ComplianceFinding] = []
    if today is None:
        today = datetime.datetime.now(datetime.UTC).date()
    identity = f"coa:{snapshot.supplier_id}:{snapshot.material_lot}"
    anchor = _anchor(
        snapshot.source_uri,
        identity,
        artifact_sha256=snapshot.attachment_sha256,
    )

    if not snapshot.signed:
        findings.append(
            ComplianceFinding(
                finding_type=ComplianceFindingType.COMPLETENESS,
                field_name="coa_signature",
                label="Supplier CoA signature",
                root_cause_category=RootCauseCategory.MATERIAL,
                description="Supplier CoA is not signed.",
                anchor=anchor,
            )
        )
    if not snapshot.quality_inspection_accepted:
        findings.append(
            ComplianceFinding(
                finding_type=ComplianceFindingType.OUT_OF_SPECIFICATION,
                field_name="quality_inspection",
                label="Incoming material quality inspection",
                root_cause_category=RootCauseCategory.MATERIAL,
                description="ERPNext incoming material quality inspection is not accepted.",
                anchor=anchor,
                oos_phase=OosPhase.PHASE_I_LABORATORY,
            )
        )
    if snapshot.material_lot != snapshot.dispensed_lot:
        findings.append(
            ComplianceFinding(
                finding_type=ComplianceFindingType.OUT_OF_SPECIFICATION,
                field_name="material_lot",
                label="CoA material lot",
                root_cause_category=RootCauseCategory.MATERIAL,
                description=(
                    f"CoA lot {snapshot.material_lot} does not match "
                    f"dispensed lot {snapshot.dispensed_lot}."
                ),
                anchor=anchor,
                observed_value=snapshot.material_lot,
                expected_value=snapshot.dispensed_lot,
                oos_phase=OosPhase.PHASE_I_LABORATORY,
            )
        )
    if not snapshot.gmp_certificate_current:
        findings.append(
            ComplianceFinding(
                finding_type=ComplianceFindingType.COMPLETENESS,
                field_name="gmp_certificate",
                label="Supplier GMP certificate (recorded expiry)",
                root_cause_category=RootCauseCategory.MATERIAL,
                description="Recorded GMP certificate expiry is not current or the certificate is missing.",
                anchor=anchor,
            )
        )
    if snapshot.retest_date < today:
        findings.append(
            ComplianceFinding(
                finding_type=ComplianceFindingType.OUT_OF_SPECIFICATION,
                field_name="retest_date",
                label="Supplier CoA retest date",
                root_cause_category=RootCauseCategory.MATERIAL,
                description=(
                    f"Supplier CoA retest date {snapshot.retest_date.isoformat()} "
                    f"is before {today.isoformat()}."
                ),
                anchor=anchor,
                observed_value=snapshot.retest_date.isoformat(),
                expected_value=f">= {today.isoformat()}",
                oos_phase=OosPhase.PHASE_I_LABORATORY,
            )
        )
    return findings
