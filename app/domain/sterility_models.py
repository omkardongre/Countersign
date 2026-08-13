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

"""14-day sterility incubation records. External state, not an LLM."""

from __future__ import annotations

import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class SterilityIncubationStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"


class SterilityIncubationRecord(BaseModel):
    """Per-batch incubation clock. Completing it is not a batch release."""

    tenant_id: str
    batch_id: str
    case_id: str
    status: SterilityIncubationStatus
    started_at: datetime.datetime
    due_at: datetime.datetime
    completed_at: datetime.datetime | None = None
    hold_seconds: int
    compressed_for_demo: bool = False
    workflow_version: str
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Sterility records cannot claim decision authority.")
        return value


class SterilityHoldBrief(BaseModel):
    """Shown while the workflow is checkpointed on the incubation hold."""

    tenant_id: str
    case_id: str
    batch_id: str
    status: SterilityIncubationStatus
    started_at: datetime.datetime
    due_at: datetime.datetime
    hold_seconds: int
    compressed_for_demo: bool
    workflow_version: str
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Sterility hold brief cannot claim decision authority.")
        return value


class SterilityTickRequest(BaseModel):
    """Scheduler/lab signal. The store, not this payload, is authoritative."""

    tenant_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
