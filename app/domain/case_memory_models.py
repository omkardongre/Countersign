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

"""Case Memory: prior-deviation facts, not a chat transcript.

Deliberately not ``VertexAiMemoryBankService`` — see
``final_reseach/05_architecture.md`` section 4.6. That service's
``search_memory`` drops ``custom_metadata``/``id`` on read (provenance
cannot round-trip), scopes only by ``app_name``/``user_id`` (no tenant
filter), and has no delete method (a poisoned fact cannot be redacted).

Every ``CaseMemoryFact`` therefore carries its source and tenant alongside
the fact text, and is retrievable and redactable by ``fact_id``.
``decision_authority`` is not a field here on purpose: a memory fact is
retrieved evidence for the Investigation agent to weigh, never a
conclusion, so it carries no authority field to lock in the first place.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class CaseMemoryFact(BaseModel):
    """One retrievable fact about a prior deviation, with its source.

    ``fact`` and ``source`` are kept as separate fields (not one blob) so a
    retrieval result and its provenance can never be pasted together loosely
    the way a single string would allow.
    """

    fact_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    case_id: str
    batch_id: str
    fact: str
    source_uri: str
    confidence: float = Field(ge=0.0, le=1.0)
    observed_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    redacted: bool = False
    redacted_reason: str | None = None


class CaseMemoryQueryResult(BaseModel):
    """Retrieval result for one prior-art query, tenant-filtered.

    ``query_embedded`` records whether semantic (vector) retrieval ran, so a
    caller can tell "no similar priors" apart from "embeddings were
    unavailable and this fell back to keyword search" — the two are not the
    same finding.
    """

    tenant_id: str
    query: str
    facts: list[CaseMemoryFact] = Field(default_factory=list)
    query_embedded: bool = False
