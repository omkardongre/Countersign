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


class Department(StrEnum):
    MANUFACTURING = "manufacturing"
    QC = "qc"
    SUPPLY = "supply"


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


class DepartmentSnapshot(BaseModel):
    tenant_id: str
    batch_id: str
    product_code: str
    lims: LimsSnapshot
    em: EmSnapshot
    coa: CoaSnapshot


class CaseEvidenceBundle(BaseModel):
    """Shared fan-out input: classified scope plus the manufacturing BMR."""

    tenant_id: str
    case_id: str
    batch_id: str
    bmr: EvidenceIngestRequest


class BranchOutcome(BaseModel):
    """Always emitted so a JoinNode cannot stall on a failed department."""

    department: Department
    status: BranchStatus
    error: str | None = None
    source_uris: list[str] = Field(default_factory=list)
    findings: list[ComplianceFinding] = Field(default_factory=list)
    manufacturing_compliance: ComplianceResult | None = None
    decision_authority: str = "none"
