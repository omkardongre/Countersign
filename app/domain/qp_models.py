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

"""QP confirmation types. CDMO confirmation, not MAH certification."""

from __future__ import annotations

import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.domain.release_models import CaseReleasePackage, ReleaseGateStatus


class QpConfirmationAction(StrEnum):
    CONFIRM = "confirm"
    DECLINE = "decline"
    CANNOT_CONFIRM = "cannot_confirm"


class QpConfirmationOutcome(StrEnum):
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    CANNOT_CONFIRM = "cannot_confirm"
    REJECTED = "rejected"


class QpConfirmationRequest(BaseModel):
    """Structured reply the QP must submit. Not a batch release."""

    action: QpConfirmationAction
    qp_name: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class QpReviewBrief(BaseModel):
    """What the HITL client is shown. The gate outcome is not rewritten."""

    tenant_id: str
    case_id: str
    batch_id: str
    release_gate_status: ReleaseGateStatus
    blocked_reasons: list[str] = Field(default_factory=list)
    spec_id: str
    spec_version: str
    spec_sha256: str
    investigation_gate_passed: bool
    workflow_name: str
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("QP brief cannot claim decision authority.")
        return value


class QpConfirmationRecord(BaseModel):
    """Immutable record of the human act. Machine types stay authority none."""

    action: QpConfirmationAction
    outcome: QpConfirmationOutcome
    qp_name: str
    rationale: str
    tenant_id: str
    case_id: str
    batch_id: str
    gate_status: ReleaseGateStatus
    recorded_at: datetime.datetime
    rejection_reason: str | None = None
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("QP confirmation record cannot claim decision authority.")
        return value


class QpReviewedPackage(BaseModel):
    """Workflow output after the QP has answered the confirmation prompt."""

    release_package: CaseReleasePackage
    qp_confirmation: QpConfirmationRecord
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Reviewed package cannot claim decision authority.")
        return value
