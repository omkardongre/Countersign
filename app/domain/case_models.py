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

"""Payloads that join case intake to the first evidence page.

These are graph-boundary types, not the case record itself. Tenant, case and
batch scope are stamped from the classified case onto the ingest request so an
evidence file cannot claim a different sponsor than the deviation it was
opened under (Annex 16 §1.4.3).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.compliance_models import (
    ComplianceFinding,
    ComplianceResult,
    ComplianceStatus,
)
from app.domain.department_models import BranchOutcome
from app.domain.models import CaseIntake, ClassifiedCase


class EvidenceAttachment(BaseModel):
    """Retrieved evidence bytes attached at case intake, without scope claims."""

    source_uri: str
    mime_type: str
    content_base64: str
    page_count: int = Field(default=1, ge=1)
    spec_version_expected: str | None = None


class DeviationCaseRequest(BaseModel):
    """START input for the joined deviation-investigation graph."""

    intake: CaseIntake
    evidence: EvidenceAttachment


class CaseComplianceResult(BaseModel):
    """Classified case plus joined department evidence.

    ``decision_authority`` stays ``"none"``: this is an investigation brief,
    not a batch disposition. ``compliance`` is the manufacturing-page engine
    result when that branch succeeded; ``aggregated_status`` is the join of
    all departments.
    """

    classified: ClassifiedCase
    compliance: ComplianceResult | None = None
    aggregated_status: ComplianceStatus
    branches: list[BranchOutcome] = Field(default_factory=list)
    findings: list[ComplianceFinding] = Field(default_factory=list)
    decision_authority: str = "none"
