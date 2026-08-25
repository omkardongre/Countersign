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

"""Typed references to CFI external artifacts.

The references are part of the durable CFI aggregate. They are not evidence
and do not grant decision authority: they tell the desk where the
site-review calendar event and filed CFI record can be inspected.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field, field_validator


class CfiSiteReviewReference(BaseModel):
    calendar_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    event_url: str = Field(min_length=1)
    start_at: datetime.datetime
    end_at: datetime.datetime
    attendees: tuple[str, ...] = ()
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI site-review references cannot claim authority.")
        return value


class CfiReportReference(BaseModel):
    document_id: str = Field(min_length=1)
    document_url: str = Field(min_length=1)
    was_update: bool = False
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI report references cannot claim authority.")
        return value


class CfiSiteReviewRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    case_tenant_id: str = Field(min_length=1)
    cfi_id: str = Field(min_length=1)
    source_case_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    closed_at: datetime.datetime
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI site-review requests cannot claim authority.")
        return value


class CfiReportWriteRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    case_tenant_id: str = Field(min_length=1)
    cfi_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=100_000)
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI report requests cannot claim authority.")
        return value


class CfiReportWriteResult(BaseModel):
    document_id: str = Field(min_length=1)
    document_url: str = Field(min_length=1)
    was_update: bool = False
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI report results cannot claim authority.")
        return value
