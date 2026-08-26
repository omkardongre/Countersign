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

"""Doc 12 T1-5: controlled-SOP and public-guidance RAG citation types.

Two corpora, never merged into one retrieval space:

* ``CONTROLLED_SOP`` — the tenant's own approved, change-controlled Standard
  Operating Procedures. A citation here can support the deterministic
  "investigation cited an in-force controlled document" gate requirement.
* ``PUBLIC_GUIDANCE`` — Annex 1, ICH Q9(R1), OOS guidance and similar public
  regulatory text. Advisory context only: it can never satisfy the
  controlled-SOP citation requirement and never grounds a batch fact, root
  cause, or disposition (``final_reseach/10_status_and_remaining_work.md``
  T1-5 guardrail).

Vertex AI RAG Engine's own retrieval output
(``docs.cloud.google.com/vertex-ai/generative-ai/docs/model-reference/
rag-output-explained``) is only ``source_uri``/``source_display_name``/
``text``/``score`` — no version, approval, or effective-date. That is not
enough for a GMP citation, so every retrieved chunk is cross-checked against
``app/domain/guidance_manifest.py``'s change-controlled manifest before it
becomes a ``GuidanceCitation``. A retrieved chunk whose ``source_uri`` is not
in the manifest is dropped, not trusted — an unverifiable citation is worse
than no citation.
"""

from __future__ import annotations

import datetime
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class GuidanceCorpus(StrEnum):
    CONTROLLED_SOP = "controlled_sop"
    PUBLIC_GUIDANCE = "public_guidance"


class ControlledGuidanceDocument(BaseModel):
    """One change-controlled entry in the guidance manifest (master data).

    Not a retrieval result: this is the document-control record deterministic
    code checks a retrieved chunk against. ``source_uri`` must match exactly
    what Vertex AI RAG Engine reports as the file's original Cloud Storage or
    Drive URI at import time, so a retrieved chunk can be looked up by it.
    """

    model_config = ConfigDict(frozen=True)

    doc_id: str
    corpus: GuidanceCorpus
    title: str
    version: str
    effective_date: datetime.date
    approved: bool
    superseded: bool = False
    jurisdiction: str | None = None
    tenant_id: str | None = Field(
        default=None,
        description=(
            "Set for a tenant-owned controlled SOP; None for a document "
            "shared across tenants (public guidance, or a corporate SOP). "
            "A retrieved chunk whose document has a tenant_id that does not "
            "match the querying case's tenant is dropped, the same "
            "pre-filter discipline Case Memory's VECTOR_SEARCH already "
            "applies, enforced here in the manifest lookup because the "
            "underlying RAG corpus is not itself tenant-partitioned."
        ),
    )
    source_uri: str

    @property
    def in_force(self) -> bool:
        return self.approved and not self.superseded


class GuidanceCitation(BaseModel):
    """One retrieved chunk, after manifest cross-check. Real retrieval only.

    ``similarity_score`` is the real value Vertex AI RAG Engine's own
    retrieval reports (never estimated or backfilled — same discipline as
    ``CaseMemoryFact.similarity_score``).
    """

    citation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    corpus: GuidanceCorpus
    doc_id: str
    title: str
    version: str
    effective_date: datetime.date
    approved: bool
    superseded: bool
    source_uri: str
    text: str
    similarity_score: float | None = None
    retrieved_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )

    @property
    def in_force(self) -> bool:
        return self.approved and not self.superseded


class GuidanceQueryResult(BaseModel):
    """Retrieval result for one guidance query.

    ``configured`` distinguishes "corpora not provisioned in this
    deployment" from "searched and found nothing" — the same "tell the two
    apart" discipline ``CaseMemoryQueryResult.query_embedded`` already has.
    """

    tenant_id: str
    query: str
    citations: list[GuidanceCitation] = Field(default_factory=list)
    configured: bool = False
