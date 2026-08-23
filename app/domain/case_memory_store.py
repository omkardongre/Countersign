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

"""Case Memory store: Firestore + Vertex text embeddings, tenant-filtered.

Runs inside the department MCP process (same credential shape as
``sterility_store.py``/``department_tools.py``): an Agent Identity principal
cannot itself hold Firestore IAM credentials any more than it can hold a
Cloud SQL IAM one, so Case Memory is reached over the ``prior_art_*`` MCP
tools, not opened directly by the Runtime.

Retrieval is embedding-based (``text-embedding-005``, matching the model
used throughout ``research/reference-repos``) via Firestore's native
``find_nearest`` KNN query — not the keyword-``array_contains`` approach in
``google.adk.integrations.firestore.FirestoreMemoryService`` — because
"similar deviation" is a semantic match, not a shared-word match, and the
architecture explicitly rejected keyword/no-tenant-filter/no-redact designs
(``final_reseach/05_architecture.md`` section 4.6).

If embeddings are unavailable (no Firestore vector index yet provisioned,
or the embedding call fails), retrieval falls back to a tenant-scoped
recency query rather than raising — Case Memory is advisory input to the
Investigation agent, not something that should take a case down. The
fallback is always reported via ``query_embedded=False`` so it is never
confused with "searched and found nothing similar".
"""

from __future__ import annotations

import functools
import os
from typing import TYPE_CHECKING, Protocol

from app.domain.case_memory_models import CaseMemoryFact

if TYPE_CHECKING:
    from google.cloud import firestore

EMBEDDING_MODEL = "text-embedding-005"
CASE_MEMORY_COLLECTION_ENV = "COUNTERSIGN_CASE_MEMORY_COLLECTION"
DEFAULT_COLLECTION = "case_memory_facts"
DEFAULT_TOP_K = 5


class EmbeddingUnavailable(Exception):
    """The embedding call failed or returned no vector. Caller should fall back."""


async def embed_text(text: str) -> list[float]:
    """One text embedding via the Vertex-backed ``google-genai`` client.

    Uses ``client.aio.models.embed_content`` (``google.genai``, already a
    transitive dependency of ``google-adk``), not a separate embeddings SDK.
    """
    from google import genai

    client = genai.Client(
        vertexai=True,
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        location="us-central1",
    )
    try:
        response = await client.aio.models.embed_content(
            model=EMBEDDING_MODEL, contents=[text]
        )
    except Exception as exc:
        raise EmbeddingUnavailable(str(exc)) from exc
    embeddings = getattr(response, "embeddings", None)
    if not embeddings:
        raise EmbeddingUnavailable("Embedding response contained no vectors.")
    values = embeddings[0].values
    if not values:
        raise EmbeddingUnavailable("Embedding response vector was empty.")
    return list(values)


class CaseMemoryStore(Protocol):
    async def write_fact(
        self,
        *,
        tenant_id: str,
        case_id: str,
        batch_id: str,
        fact: str,
        source_uri: str,
        confidence: float,
        fact_id: str | None = None,
    ) -> CaseMemoryFact: ...

    async def query(
        self, *, tenant_id: str, query: str, top_k: int = DEFAULT_TOP_K
    ) -> tuple[list[CaseMemoryFact], bool]: ...

    async def redact(
        self, *, tenant_id: str, fact_id: str, reason: str
    ) -> CaseMemoryFact: ...

    async def latest_fact_for_batch(
        self, *, tenant_id: str, batch_id: str
    ) -> CaseMemoryFact | None: ...


class FirestoreCaseMemoryStore:
    """Firestore-backed store. Tenant filter applied before retrieval."""

    def __init__(
        self,
        client: firestore.AsyncClient | None = None,
        collection: str | None = None,
    ) -> None:
        if client is None:
            from google.cloud import firestore as firestore_module

            project = os.environ.get("GOOGLE_CLOUD_PROJECT") or None
            database = os.environ.get("COUNTERSIGN_CASE_MEMORY_DATABASE", "(default)")
            self._client = firestore_module.AsyncClient(
                project=project, database=database
            )
        else:
            self._client = client
        self._collection_name = collection or os.environ.get(
            CASE_MEMORY_COLLECTION_ENV, DEFAULT_COLLECTION
        )

    def _collection(self):
        return self._client.collection(self._collection_name)

    async def write_fact(
        self,
        *,
        tenant_id: str,
        case_id: str,
        batch_id: str,
        fact: str,
        source_uri: str,
        confidence: float,
        fact_id: str | None = None,
    ) -> CaseMemoryFact:
        """``fact_id`` is optional so a caller with a natural idempotency
        key (e.g. ``record_case_memory`` deriving one from ``case_id`` plus
        outcome) can pass it and have a rerun overwrite the same document
        instead of writing a duplicate with a fresh random id.
        """
        kwargs = {
            "tenant_id": tenant_id,
            "case_id": case_id,
            "batch_id": batch_id,
            "fact": fact,
            "source_uri": source_uri,
            "confidence": confidence,
        }
        if fact_id is not None:
            kwargs["fact_id"] = fact_id
        record = CaseMemoryFact(**kwargs)
        doc = {
            "tenant_id": record.tenant_id,
            "case_id": record.case_id,
            "batch_id": record.batch_id,
            "fact": record.fact,
            "source_uri": record.source_uri,
            "confidence": record.confidence,
            "observed_at": record.observed_at,
            "redacted": record.redacted,
            "redacted_reason": record.redacted_reason,
        }
        try:
            from google.cloud.firestore_v1.vector import Vector

            doc["embedding"] = Vector(await embed_text(fact))
        except EmbeddingUnavailable:
            # Fact is still written without a vector; it remains reachable
            # by the recency fallback, just not by semantic search until a
            # later backfill embeds it.
            pass
        # set() (not create()) so a resume-triggered rerun with the same
        # fact_id overwrites this document instead of raising or duplicating.
        await self._collection().document(record.fact_id).set(doc)
        return record

    def _to_fact(self, data: dict) -> CaseMemoryFact:
        return CaseMemoryFact(
            fact_id=data["fact_id"],
            tenant_id=data["tenant_id"],
            case_id=data["case_id"],
            batch_id=data["batch_id"],
            fact=data["fact"],
            source_uri=data["source_uri"],
            confidence=data["confidence"],
            observed_at=data["observed_at"],
            redacted=data.get("redacted", False),
            redacted_reason=data.get("redacted_reason"),
        )

    async def query(
        self, *, tenant_id: str, query: str, top_k: int = DEFAULT_TOP_K
    ) -> tuple[list[CaseMemoryFact], bool]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        base = self._collection().where(
            filter=FieldFilter("tenant_id", "==", tenant_id)
        ).where(filter=FieldFilter("redacted", "==", False))

        try:
            vector = await embed_text(query)
        except EmbeddingUnavailable:
            docs = await base.order_by(
                "observed_at", direction="DESCENDING"
            ).limit(top_k).get()
            facts = [self._fact_from_doc(doc) for doc in docs]
            return facts, False

        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        docs = await base.find_nearest(
            vector_field="embedding",
            query_vector=Vector(vector),
            distance_measure=DistanceMeasure.COSINE,
            limit=top_k,
        ).get()
        facts = [self._fact_from_doc(doc) for doc in docs]
        return facts, True

    def _fact_from_doc(self, doc) -> CaseMemoryFact:
        data = doc.to_dict() or {}
        data["fact_id"] = doc.id
        return self._to_fact(data)

    async def redact(
        self, *, tenant_id: str, fact_id: str, reason: str
    ) -> CaseMemoryFact:
        doc_ref = self._collection().document(fact_id)
        snapshot = await doc_ref.get()
        if not snapshot.exists:
            raise KeyError(f"No Case Memory fact {fact_id!r}.")
        data = snapshot.to_dict() or {}
        if data.get("tenant_id") != tenant_id:
            raise KeyError(
                f"Fact {fact_id!r} does not belong to tenant {tenant_id!r}."
            )
        await doc_ref.update({"redacted": True, "redacted_reason": reason})
        data["fact_id"] = fact_id
        data["redacted"] = True
        data["redacted_reason"] = reason
        return self._to_fact(data)

    async def latest_fact_for_batch(
        self, *, tenant_id: str, batch_id: str
    ) -> CaseMemoryFact | None:
        """Most recent non-redacted fact for this batch, tenant-scoped.

        Backs the sponsor liaison's QP-outcome lookup
        (``sponsor_tools.py``): ``record_case_memory`` writes exactly one
        fact per case at ``qp_outcome:{tenant_id}:{case_id}``, so the most
        recent fact for a batch *is* its latest QP outcome. No vector search
        here — this is an exact tenant+batch filter, not a similarity query.
        """
        from google.cloud.firestore_v1.base_query import FieldFilter

        docs = await (
            self._collection()
            .where(filter=FieldFilter("tenant_id", "==", tenant_id))
            .where(filter=FieldFilter("batch_id", "==", batch_id))
            .where(filter=FieldFilter("redacted", "==", False))
            .order_by("observed_at", direction="DESCENDING")
            .limit(1)
            .get()
        )
        for doc in docs:
            return self._fact_from_doc(doc)
        return None


class InMemoryCaseMemoryStore:
    """Local/test store: no Firestore, no embedding calls, tenant-filtered."""

    def __init__(self) -> None:
        self._facts: dict[str, CaseMemoryFact] = {}

    async def write_fact(
        self,
        *,
        tenant_id: str,
        case_id: str,
        batch_id: str,
        fact: str,
        source_uri: str,
        confidence: float,
        fact_id: str | None = None,
    ) -> CaseMemoryFact:
        kwargs = {
            "tenant_id": tenant_id,
            "case_id": case_id,
            "batch_id": batch_id,
            "fact": fact,
            "source_uri": source_uri,
            "confidence": confidence,
        }
        if fact_id is not None:
            kwargs["fact_id"] = fact_id
        record = CaseMemoryFact(**kwargs)
        self._facts[record.fact_id] = record
        return record

    async def query(
        self, *, tenant_id: str, query: str, top_k: int = DEFAULT_TOP_K
    ) -> tuple[list[CaseMemoryFact], bool]:
        del query  # No embeddings locally; substring match keeps tests deterministic.
        matches = [
            f
            for f in self._facts.values()
            if f.tenant_id == tenant_id and not f.redacted
        ]
        matches.sort(key=lambda f: f.observed_at, reverse=True)
        return matches[:top_k], False

    async def redact(
        self, *, tenant_id: str, fact_id: str, reason: str
    ) -> CaseMemoryFact:
        record = self._facts.get(fact_id)
        if record is None or record.tenant_id != tenant_id:
            raise KeyError(f"No Case Memory fact {fact_id!r} for tenant {tenant_id!r}.")
        updated = record.model_copy(update={"redacted": True, "redacted_reason": reason})
        self._facts[fact_id] = updated
        return updated

    async def latest_fact_for_batch(
        self, *, tenant_id: str, batch_id: str
    ) -> CaseMemoryFact | None:
        matches = [
            f
            for f in self._facts.values()
            if f.tenant_id == tenant_id and f.batch_id == batch_id and not f.redacted
        ]
        if not matches:
            return None
        return max(matches, key=lambda f: f.observed_at)

    def reset(self) -> None:
        self._facts.clear()


_INMEMORY_ENV = "COUNTERSIGN_CASE_MEMORY_INMEMORY"


@functools.cache
def get_case_memory_store() -> CaseMemoryStore:
    """Firestore by default; explicit opt-out for local/test runs.

    Unlike the sterility store, there is no Cloud SQL connection string to
    branch on here — Firestore has no equivalent "not configured" signal
    short of a failed client call, so the switch is an explicit env flag
    instead, matching ``COUNTERSIGN_MCP_INPROCESS``'s opt-in shape.
    """
    if os.environ.get(_INMEMORY_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return InMemoryCaseMemoryStore()
    return FirestoreCaseMemoryStore()


def reset_case_memory_store_cache() -> None:
    get_case_memory_store.cache_clear()
