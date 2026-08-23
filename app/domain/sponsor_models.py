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

"""Cross-company batch-status types for the sponsor liaison.

Per ``final_reseach/10_gap_analysis_and_win_plan.md`` section 3.5: the
white-space this fleet fills is a sponsor's QA agent asking a CDMO's case
agent for one batch's confirmation status, across a company boundary, where
the CDMO's own gateway allows exactly one scoped question and denies
everything else.

``SponsorBatchStatus`` is deliberately narrow. It is not
``CaseComplianceResult`` or ``QpReviewedPackage`` with fields removed after
the fact — it is its own model, so a future change to the internal case
record cannot silently widen what a sponsor can see just by adding a field
upstream. No findings, no citations, no investigation text, no internal
``case_id``. A sponsor learns whether their own batch is on hold, confirmed,
or still under investigation, and nothing about how.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class SponsorBatchLifecycleView(StrEnum):
    """What a sponsor is allowed to know about where a batch stands.

    Coarser than the internal ``LifecycleState`` on purpose: a sponsor does
    not need, and must not receive, which department branch failed or which
    Annex 16 item is unevaluated.

    Deliberately only as many values as this slice can actually prove from a
    real store, not a complete lifecycle model with placeholders filled in
    for stages the sponsor tools cannot yet observe. There is no batch-keyed
    index of "a case is open and mid-investigation" — cases live in ADK
    session state, keyed by session, not by batch — so that stage is not
    represented here rather than guessed at. ``NOT_FOUND`` covers both "no
    case has reached a checkpoint yet" and "no case exists"; the sponsor
    tools do not claim to distinguish them, because neither backing store
    lets them.
    """

    ON_STERILITY_HOLD = "on_sterility_hold"
    QP_CONFIRMED = "qp_confirmed"
    QP_DECLINED = "qp_declined"
    QP_COULD_NOT_CONFIRM = "qp_could_not_confirm"
    NOT_FOUND = "not_found"


class SponsorBatchStatusQuery(BaseModel):
    """The one question a sponsor's agent may ask, across the company boundary.

    ``sponsor_id`` identifies the calling company, never the calling person —
    the scope check in ``sponsor_tools.py`` is sponsor-to-tenant, matching the
    CDMO's actual Technical Quality Agreement with that sponsor, not a
    per-user permission.
    """

    sponsor_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)


class SponsorBatchStatusResult(BaseModel):
    """The scoped answer. ``decision_authority`` stays ``"none"``: this is a
    status read, never a release confirmation the sponsor could rely on as
    one.
    """

    tenant_id: str
    batch_id: str
    lifecycle: SponsorBatchLifecycleView
    sterility_hold_due_at: str | None = None
    qp_confirmed_at: str | None = None
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Sponsor batch status cannot claim decision authority.")
        return value


class SponsorScopeDenied(Exception):
    """The calling sponsor is not party to this tenant's Quality Agreement.

    Raised, never silently answered with an empty result, so a denial is
    distinguishable in logs from "batch not found" — the same distinction
    the TQA ``ext_proc`` callout makes for tenant/lifecycle denials
    (``infra/geap/tqa_spike``).
    """
