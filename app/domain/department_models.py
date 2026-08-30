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

"""Department-system payloads in LIMS / EM / CoA connector shapes.

These are the rows MCP Toolbox ``postgres-sql`` / BigQuery tools would return.
Tenant and case ids are stamped from the classified case, not from the source.
"""

from __future__ import annotations

import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.compliance_models import ComplianceFinding, ComplianceResult
from app.domain.evidence_models import EvidenceIngestRequest
from app.domain.regulatory_screening_models import (
    RegulatoryScreeningBundle,
    ScreeningAdvisoryFinding,
)


class Department(StrEnum):
    MANUFACTURING = "manufacturing"
    QC = "qc"
    ENVIRONMENTAL_MONITORING = "environmental_monitoring"
    SUPPLY = "supply"
    MAINTENANCE = "maintenance"


class BranchStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"


class LimsResultRow(BaseModel):
    """One LIMS test result as Cloud SQL would return it."""

    test_code: str
    label: str
    result_value: float
    unit: str
    spec_min: float
    spec_max: float
    sample_id: str


class LimsSnapshot(BaseModel):
    system: str = "cloud_sql"
    source_uri: str
    rows: list[LimsResultRow] = Field(default_factory=list)


class EmReadingRow(BaseModel):
    """One environmental-monitoring point as BigQuery would return it."""

    location_id: str
    parameter: str
    label: str
    value: float
    unit: str
    alert_limit: float
    action_limit: float
    recorded_at: datetime.datetime


class EmSnapshot(BaseModel):
    system: str = "bigquery"
    source_uri: str
    rows: list[EmReadingRow] = Field(default_factory=list)


class CoaSnapshot(BaseModel):
    """Supplier CoA as a Drive-normalized record (PDF bytes attach later)."""

    system: str = "drive"
    source_uri: str
    supplier_id: str
    material_lot: str
    dispensed_lot: str
    signed: bool
    retest_date: datetime.date
    gmp_certificate_current: bool
    quality_inspection_accepted: bool = True
    record_id: str | None = None
    record_modified: datetime.datetime | None = None
    quality_inspection_id: str | None = None
    attachment_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    # Public attributes are populated only by a real CoA/source adapter. They
    # are intentionally separate from internal supplier/lot identifiers so
    # regulator tools cannot accidentally transmit case-confidential data.
    manufacturer_name: str | None = None
    manufacturer_country: str | None = None
    manufacturer_city: str | None = None
    oms_location_id: str | None = None
    public_product_name: str | None = None
    public_active_ingredient: str | None = None
    public_defect_signature: str | None = None


class MaintenanceWorkOrder(BaseModel):
    """A maintenance process record returned by openMAINT.

    The external process number remains evidence, rather than a Countersign
    generated identifier.  ``closed`` is deliberately explicit because an
    unresolved corrective intervention is itself a GMP-relevant condition.
    """

    record_id: str
    process_number: str
    status: str
    opened_at: datetime.datetime | None = None
    closed_at: datetime.datetime | None = None
    closed: bool
    source_uri: str


class MaintenanceSnapshot(BaseModel):
    """Read-only maintenance evidence for the ERPNext-bound equipment."""

    system: str = "openmaint"
    source_uri: str
    asset_code: str
    asset_name: str
    preventive_due_at: datetime.datetime | None = None
    open_corrective_count: int = Field(ge=0)
    corrective_work_orders: list[MaintenanceWorkOrder] = Field(default_factory=list)
    # ERPNext owns the batch execution window; retain it on the captured
    # evidence snapshot so deterministic checks can correlate it with the
    # independent openMAINT history.
    batch_window_started_at: datetime.datetime | None = None
    batch_window_ended_at: datetime.datetime | None = None


class MaintenanceAssetBinding(BaseModel):
    """Equipment and batch window resolved from an ERPNext Work Order.

    This is deliberately separate from the openMAINT snapshot: ERPNext owns
    the batch-to-workstation genealogy; openMAINT owns asset history.
    """

    asset_code: str = Field(min_length=1, max_length=128)
    work_order_id: str = Field(min_length=1, max_length=256)
    source_uri: str = Field(min_length=1, max_length=4096)
    batch_window_started_at: datetime.datetime
    batch_window_ended_at: datetime.datetime


class DriveEvidencePayload(BaseModel):
    """One scanned BMR page fetched live from Google Drive.

    Distinct from ``CoaSnapshot`` (a synthetic catalog row that only
    *claims* to be Drive-shaped): this carries real bytes read from the
    Drive API, ready to hand to ``EvidenceIngestRequest``/
    ``decode_and_verify_payload`` unchanged.
    """

    system: str = "drive"
    source_uri: str
    file_id: str
    mime_type: str
    content_base64: str
    page_count: int = 1


class DepartmentSnapshot(BaseModel):
    tenant_id: str
    batch_id: str
    product_code: str
    lims: LimsSnapshot
    em: EmSnapshot
    coa: CoaSnapshot


class CaseEvidenceBundle(BaseModel):
    """Shared fan-out input: classified scope plus the manufacturing BMR.

    ``drive_file_id`` set means ``collect_manufacturing`` pulls the page's
    real bytes/MIME type over the ``drive_fetch_evidence`` MCP tool before
    ingest, ignoring whatever placeholder is in ``bmr``. Unset keeps the
    fixture path: ``bmr`` already carries real bytes.
    """

    tenant_id: str
    case_id: str
    batch_id: str
    lifecycle_state: str = "classified"
    bmr: EvidenceIngestRequest
    drive_file_id: str | None = None


class BranchOutcome(BaseModel):
    """Always emitted so a JoinNode cannot stall on a failed department."""

    department: Department
    status: BranchStatus
    error: str | None = None
    source_uris: list[str] = Field(default_factory=list)
    findings: list[ComplianceFinding] = Field(default_factory=list)
    # Public-regulator context is advisory only and deliberately separate
    # from ComplianceFinding, which participates in investigation/release
    # gating. These values never establish supplier clearance.
    regulatory_screening: RegulatoryScreeningBundle | None = None
    advisory_findings: list[ScreeningAdvisoryFinding] = Field(default_factory=list)
    manufacturing_compliance: ComplianceResult | None = None
    decision_authority: str = "none"
