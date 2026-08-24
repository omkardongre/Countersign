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

"""Types for the filed Google Doc investigation report + Annex 16 package."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class QmsReportWriteRequest(BaseModel):
    """What the ``qa_qms_write_agent`` tool call submits.

    ``title``/``body`` are pre-built deterministic text
    (``app.domain.qms_report.build_report_title``/``build_report_body``); the
    tool never asks the model to draft report content.
    """

    tenant_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)


class QmsReportWriteResult(BaseModel):
    """Result of filing (or re-filing) the report. Never a disposition."""

    document_id: str
    document_url: str
    was_update: bool
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("QMS report write result cannot claim decision authority.")
        return value
