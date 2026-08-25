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

"""CFI Phase 3 trend-correlation domain models.

Countersign's own read-only catalog discovery (2026-08-25) found exactly
three real, tenant-locked sources this deployment can query for historical
comparison: environmental-monitoring history (BigQuery), cross-batch LIMS
assay results (Cloud SQL), and this tenant's own prior CFI count (Cloud
SQL). There is no complaint or Annual Product Review table in this
deployment. ``CfiTrendQueryKind`` is therefore deliberately limited to those
three — a fourth kind is not added speculatively, and no synthetic
complaint/APR fallback exists anywhere in this module.

The model chooses which historical comparison is relevant
(``CfiTrendRequest``); it never supplies a tenant, a raw SQL string, a table
name, or an unbounded date range. ``cfi_trend_query``
(``app/domain/department_tools.py``) executes exactly one of three fixed,
parameterized query templates and returns an association only, per every
other CFI enquiry kind's own guardrail (never itself a determination of
common cause).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class CfiTrendQueryKind(StrEnum):
    """The one real, tenant-locked comparison ``cfi_trend_query`` may run.

    ``EM_PARAMETER_COMPARISON`` and ``LIMS_TEST_COMPARISON`` compare the
    CFI's own batch against one other named batch on the same tenant.
    ``PRIOR_CFI_RECURRENCE`` counts how many CFIs this tenant has already
    opened, as a recurrence signal — never a value comparison.
    """

    EM_PARAMETER_COMPARISON = "em_parameter_comparison"
    LIMS_TEST_COMPARISON = "lims_test_comparison"
    PRIOR_CFI_RECURRENCE = "prior_cfi_recurrence"


class CfiTrendRequest(BaseModel):
    """``trend_correlation_agent``'s typed output for one trend enquiry.

    ``decision_authority`` is locked to ``"none"``: this chooses *which*
    historical comparison to pull, never what it means. Only
    ``cfi_trend_query`` supplies the tenant scope; the model never does.
    """

    kind: CfiTrendQueryKind
    compare_batch_id: str | None = Field(
        default=None,
        description=(
            "The other batch on this tenant to compare against. Required "
            "for em_parameter_comparison/lims_test_comparison; ignored for "
            "prior_cfi_recurrence."
        ),
    )
    parameter: str | None = Field(
        default=None,
        description=(
            "Environmental-monitoring parameter name to compare (e.g. "
            "'particle_count'). Required for em_parameter_comparison."
        ),
    )
    test_code: str | None = Field(
        default=None,
        description=(
            "LIMS test code to compare (e.g. 'ASSAY'). Required for "
            "lims_test_comparison."
        ),
    )
    rationale: str = Field(min_length=1)
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI trend requests cannot claim decision authority.")
        return value


class CfiTrendResult(BaseModel):
    """Typed, captured result of one ``cfi_trend_query`` call.

    An association only: a value delta or a recurrence count is a lead for
    a human investigator to weigh, never itself a determination of common
    root cause (same guardrail as ``SiblingBatchQueryResult``).
    """

    kind: CfiTrendQueryKind
    tenant_id: str
    batch_id: str
    compare_batch_id: str | None = None
    parameter_or_test_code: str | None = None
    subject_mean: float | None = None
    compare_mean: float | None = None
    unit: str | None = None
    delta: float | None = None
    prior_cfi_count: int | None = None
    source_uri: str = Field(min_length=1)
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("CFI trend results cannot claim decision authority.")
        return value
