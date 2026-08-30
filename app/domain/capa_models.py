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

"""Types for the ERPNext Non Conformance + Quality Action (CAPA) write-back.

After the QP has acted (``confirm_qp``) and the investigation report has been
filed (``file_qms_report``), the ``capa_write_agent`` creates a Non Conformance
record and a linked Quality Action in ERPNext.  The body of each record is
assembled deterministically from the stored case/CFI aggregate
(``app.domain.capa_report``); the model never drafts CAPA content — it only
decides to call the two tenant/case-locked tool closures bound to it.

Every type here carries ``decision_authority: str = "none"`` with a validator
that rejects any other value, matching every other machine-produced record in
this system (Annex 22 draft posture).
"""

from __future__ import annotations

import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class CapaActionType(StrEnum):
    """ERPNext Quality Action ``action_type`` values we may create."""

    CORRECTIVE = "Corrective"
    PREVENTIVE = "Preventive"


class NonConformanceWriteRequest(BaseModel):
    """What the ``capa_write_agent`` tool call submits to create a Non
    Conformance in ERPNext.

    Maps to the real Non Conformance doctype fields:
      - ``subject``: short case summary (doctype reqd field)
      - ``details``: full NC description (Text Editor field)

    Both are pre-built deterministic text
    (``app.domain.capa_report.build_nonconformance_*``); the tool never
    asks the model to draft NC content.
    """

    tenant_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=256)
    batch_id: str = Field(min_length=1, max_length=256)
    subject: str = Field(min_length=1, max_length=256)
    details: str = Field(min_length=1, max_length=8000)


class QualityActionWriteRequest(BaseModel):
    """What the ``capa_write_agent`` tool call submits to create a Quality
    Action (CAPA) in ERPNext, linked to the Non Conformance above.

    Maps to the real Quality Action doctype fields:
      - ``action_type`` → ``corrective_preventive`` (Corrective/Preventive)
      - ``problem`` + ``resolution`` → child table ``resolutions`` row
    """

    tenant_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=256)
    batch_id: str = Field(min_length=1, max_length=256)
    non_conformance_id: str = Field(min_length=1, max_length=256)
    action_type: CapaActionType
    problem: str = Field(min_length=1, max_length=8000)
    resolution: str = Field(min_length=1, max_length=8000)


class NonConformanceWriteResult(BaseModel):
    """Result of creating (or re-using) a Non Conformance in ERPNext.

    ``was_existing`` is ``True`` when an NC with the same
    ``(tenant_id, case_id)`` key was found and re-used rather than duplicated
    — the idempotency guarantee for resume/replay safety.
    """

    non_conformance_id: str
    non_conformance_url: str
    was_existing: bool
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError(
                "Non Conformance write result cannot claim decision authority."
            )
        return value


class QualityActionWriteResult(BaseModel):
    """Result of creating (or re-using) a Quality Action in ERPNext."""

    quality_action_id: str
    quality_action_url: str
    was_existing: bool
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError(
                "Quality Action write result cannot claim decision authority."
            )
        return value


class CapaWriteResult(BaseModel):
    """Aggregate result of the CAPA write-back node.

    Combines the NC and QA results plus a visible typed error if either
    write failed.  A failure is never a silent omission — same posture as
    ``site_review_error`` in ``calendar_tools.py``.
    """

    non_conformance: NonConformanceWriteResult | None = None
    quality_action: QualityActionWriteResult | None = None
    non_conformance_error: str | None = None
    quality_action_error: str | None = None
    written_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CAPA write result cannot claim decision authority.")
        return value
