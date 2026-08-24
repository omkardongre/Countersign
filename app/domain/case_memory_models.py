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

from app.domain.master_data import RootCauseCategory


class CaseMemoryFact(BaseModel):
    """One retrievable fact about a prior deviation, with its source.

    ``fact`` and ``source`` are kept as separate fields (not one blob) so a
    retrieval result and its provenance can never be pasted together loosely
    the way a single string would allow.

    ``root_cause_category`` is descriptive metadata carried over from the
    investigation draft's own leading hypothesis at the time this fact was
    written (see ``case_memory_node.build_case_memory_fact``) — the same
    fishbone tag ``ComplianceFinding`` already carries, not a certified root
    cause. It is ``None`` whenever no hypothesis was actually drafted for
    that case, rather than defaulting to ``UNCLASSIFIED``, so a caller can
    tell "no hypothesis existed" apart from "the hypothesis was
    unclassified".

    ``similarity_score`` is populated only on a result returned by an actual
    vector search (``1 - cosine_distance`` from BigQuery's own
    ``VECTOR_SEARCH``, or the Firestore KNN case). It is ``None`` for any
    fact reached by the tenant-scoped recency fallback, an exact-match
    lookup, or a plain write — never estimated or backfilled, since a
    fabricated similarity score would be indistinguishable from a real one
    to a caller.
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
    root_cause_category: RootCauseCategory | None = None
    similarity_score: float | None = Field(default=None, ge=0.0, le=1.0)


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
