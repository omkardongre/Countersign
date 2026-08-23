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

"""Batch-keyed case status: a small, deterministic status record, not a
lifecycle event log and not Case Memory.

Exists specifically so the sponsor liaison (``sponsor_tools.py``) can answer
"where does this batch stand" from a structured field instead of parsing a
Case Memory fact's free-text sentence — text like "was confirmed by the QP
after investigation" is written for a human reviewer, and pattern-matching
it to recover a lifecycle value would be exactly the kind of fragile
shortcut this build avoids. ``CaseStatusRecord`` is written by the same two
graph nodes that already reach an external store over MCP
(``sterility_wait.py`` on park, ``qp_confirmation.py`` after the QP acts),
each stamping a real enum value at the point they already know it.
"""

from __future__ import annotations

import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CaseStatus(StrEnum):
    """Coarser than the internal ``LifecycleState``/gate-status vocabulary,
    matching exactly the values ``SponsorBatchLifecycleView`` needs — this
    store has no reason to carry a distinction the sponsor boundary discards.
    """

    ON_STERILITY_HOLD = "on_sterility_hold"
    QP_CONFIRMED = "qp_confirmed"
    QP_DECLINED = "qp_declined"
    QP_COULD_NOT_CONFIRM = "qp_could_not_confirm"


class CaseStatusRecord(BaseModel):
    tenant_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    status: CaseStatus
    sterility_due_at: datetime.datetime | None = None
    qp_confirmed_at: datetime.datetime | None = None
    updated_at: datetime.datetime
