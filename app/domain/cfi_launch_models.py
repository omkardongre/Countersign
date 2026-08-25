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

"""Scheduler-only CFI Runtime handoff models.

These payloads cross the department MCP boundary, but they do not expose
Cloud SQL credentials, raw evidence, or human replies. A launch item is a
minimal pointer to a durable CFI aggregate; the tick service starts the
separate Runtime and records the resulting invocation id through the same
least-privilege MCP process.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.domain.cfi_models import CfiLaunchStatus


class CfiLaunchItem(BaseModel):
    """One claimed CFI handoff returned to the scheduler."""

    cfi_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    source_case_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    launch_attempts: int = Field(ge=1)
    launch_status: CfiLaunchStatus
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI launch items cannot claim decision authority.")
        return value


class CfiLaunchTickResult(BaseModel):
    """Bounded scheduler claim result. No raw CFI payload crosses it."""

    items: list[CfiLaunchItem] = Field(default_factory=list)
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI launch ticks cannot claim decision authority.")
        return value


class CfiLaunchResult(BaseModel):
    """Scheduler's durable result for one claimed handoff."""

    cfi_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    launch_status: CfiLaunchStatus
    runtime_invocation_id: str | None = None
    launch_error: str | None = None
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI launch results cannot claim decision authority.")
        return value
