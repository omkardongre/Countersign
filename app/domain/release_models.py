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

"""Annex 16 section 1.7 checklist result types.

This is a CDMO confirmation pack, not a finished-product certification.
``decision_authority`` stays ``none``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.domain.investigation_models import InvestigationCaseResult


class ChecklistItemStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"


class ReleaseGateStatus(StrEnum):
    """Whether the pack may be shown to a QP. Never a certification."""

    READY_FOR_QP_REVIEW = "ready_for_qp_review"
    NOT_READY = "not_ready"


class ChecklistItemResult(BaseModel):
    annex_ref: str
    title: str
    status: ChecklistItemStatus
    evidence_uris: list[str] = Field(default_factory=list)
    reason: str
    required_for_confirmation: bool = False


class ReleaseGateResult(BaseModel):
    status: ReleaseGateStatus
    spec_id: str
    spec_version: str
    spec_sha256: str
    items: list[ChecklistItemResult]
    blocked_reasons: list[str] = Field(default_factory=list)
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Release gate cannot claim decision authority.")
        return value


class CaseReleasePackage(BaseModel):
    """Workflow output through the Annex 16 confirmation checklist."""

    investigation: InvestigationCaseResult
    release_gate: ReleaseGateResult
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Release package cannot claim decision authority.")
        return value
