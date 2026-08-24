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

"""Doc 12 T1-5: controlled-SOP and public-guidance retrieval via Vertex AI
RAG Engine, cross-checked against the change-controlled guidance manifest.

Two corpora, each configured by its own resource-name env var, exactly like
``department_source.py``'s per-department opt-in — a half-provisioned
deployment (public guidance corpus created, SOP corpus not yet imported)
must keep working on whichever corpus *is* configured, not silently fail
closed on the whole feature:

* ``COUNTERSIGN_GUIDANCE_SOP_CORPUS`` — the tenant's approved SOP corpus
  resource name (``projects/.../locations/.../ragCorpora/...``).
* ``COUNTERSIGN_GUIDANCE_PUBLIC_CORPUS`` — the public-guidance corpus
  (Annex 1, ICH Q9(R1), OOS PDFs).

Vertex AI RAG Engine's own retrieval output
(``docs.cloud.google.com/vertex-ai/generative-ai/docs/model-reference/
rag-output-explained``) carries only ``source_uri``/``source_display_name``/
``text``/``score`` — no version, approval, or effective-date. Every
retrieved chunk is therefore looked up in
``app/domain/guidance_manifest.py``'s ledger by its ``source_uri`` before it
becomes a citeable ``GuidanceCitation``: a chunk with no matching manifest
entry, whose corpus does not match, or whose ledger entry belongs to a
different tenant's controlled SOP, is dropped, not trusted on the strength
of the retrieval call alone. This is not a fallback substitute value — it is
the citation simply not existing, which is what "unverifiable" must mean
here.
"""

from __future__ import annotations

import asyncio
import functools
import os
import re
from typing import Any, Protocol

from app.domain.guidance_manifest import load_guidance_manifest
from app.domain.guidance_models import GuidanceCitation, GuidanceCorpus

SOP_CORPUS_ENV = "COUNTERSIGN_GUIDANCE_SOP_CORPUS"
PUBLIC_CORPUS_ENV = "COUNTERSIGN_GUIDANCE_PUBLIC_CORPUS"
DEFAULT_TOP_K = 5

# CEL metadata_filter literals cannot be bound as query parameters, so a
# tenant_id spliced into one is checked against this first — same reasoning
# as ``department_source.require_identifier`` for SQL identifiers. Tenant
# ids come from the classified case, not end-user input, but an unvalidated
# splice into a filter expression is an injection sink regardless of who is
# expected to set it.
_TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _cel_safe_tenant(tenant_id: str) -> str:
    if not _TENANT_ID_PATTERN.match(tenant_id):
        raise RuntimeError(f"tenant_id {tenant_id!r} is not safe for a CEL filter.")
    return tenant_id


class GuidanceSource(Protocol):
    async def query(
        self,
        *,
        tenant_id: str,
        corpus: GuidanceCorpus,
        query: str,
        top_k: int = DEFAULT_TOP_K,
    ) -> tuple[list[GuidanceCitation], bool]: ...


def _corpus_resource_name(corpus: GuidanceCorpus) -> str:
    env_name = SOP_CORPUS_ENV if corpus is GuidanceCorpus.CONTROLLED_SOP else PUBLIC_CORPUS_ENV
    return os.environ.get(env_name, "").strip()


async def _run_retrieval(
    *, corpus_name: str, query: str, top_k: int, metadata_filter: str | None
) -> list[dict[str, Any]]:
    """Runs one Vertex AI RAG Engine retrieval call off the event loop.

    ``vertexai.rag.retrieval_query`` is a synchronous, blocking gRPC call, no
    native asyncio support — same reasoning as
    ``case_memory_store_bigquery.py``'s ``_run_query`` for BigQuery's
    synchronous client: blocking the event loop here would stall every
    other in-flight MCP request on this connection.
    """

    def _call() -> list[dict[str, Any]]:
        from vertexai import rag

        rag_filter = rag.Filter(metadata_filter=metadata_filter) if metadata_filter else None
        config = rag.RagRetrievalConfig(top_k=top_k, filter=rag_filter)
        response = rag.retrieval_query(
            text=query,
            rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
            rag_retrieval_config=config,
        )
        contexts = getattr(response, "contexts", None)
        rows = getattr(contexts, "contexts", None) or []
        return [
            {
                "source_uri": row.source_uri,
                "text": row.text,
                "score": row.score if row.score else None,
            }
            for row in rows
        ]

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _call)


class VertexRagGuidanceSource:
    """Real Vertex AI RAG Engine retrieval, manifest-verified.

    ``configured`` in the return tuple means "a corpus resource name was set
    for this corpus", not "results were found" — the same "tell the two
    apart" contract ``CaseMemoryQueryResult.query_embedded`` already has.
    """

    backend_name = "vertex_rag_engine"

    async def query(
        self,
        *,
        tenant_id: str,
        corpus: GuidanceCorpus,
        query: str,
        top_k: int = DEFAULT_TOP_K,
    ) -> tuple[list[GuidanceCitation], bool]:
        corpus_name = _corpus_resource_name(corpus)
        if not corpus_name:
            return [], False

        metadata_filter: str | None = None
        if corpus is GuidanceCorpus.CONTROLLED_SOP:
            metadata_filter = f'tenant_id == "{_cel_safe_tenant(tenant_id)}"'

        rows = await _run_retrieval(
            corpus_name=corpus_name,
            query=query,
            top_k=top_k,
            metadata_filter=metadata_filter,
        )

        manifest = load_guidance_manifest()
        citations: list[GuidanceCitation] = []
        for row in rows:
            source_uri = row.get("source_uri") or ""
            document = manifest.by_source_uri(source_uri)
            if document is None:
                # Unverifiable: RAG Engine's own retrieval output carries no
                # version/approval metadata, so a chunk with no ledger entry
                # cannot become a citation, however relevant it looked.
                continue
            if document.corpus is not corpus:
                continue
            if document.tenant_id is not None and document.tenant_id != tenant_id:
                # Belongs to a different tenant's controlled SOP; the CEL
                # filter above should already exclude this at the corpus
                # level, but the manifest check is the authoritative,
                # tenant-isolation-enforcing gate, not the filter string.
                continue
            citations.append(
                GuidanceCitation(
                    tenant_id=tenant_id,
                    corpus=document.corpus,
                    doc_id=document.doc_id,
                    title=document.title,
                    version=document.version,
                    effective_date=document.effective_date,
                    approved=document.approved,
                    superseded=document.superseded,
                    source_uri=document.source_uri,
                    text=row.get("text") or "",
                    similarity_score=row.get("score"),
                )
            )
        return citations, True


class InMemoryGuidanceSource:
    """Local/test source: no Vertex call, manifest-verified, deterministic.

    Returns every manifest entry for the requested corpus, tenant-filtered
    for controlled SOPs — enough to exercise the manifest cross-check and
    the citation gate without a live RAG corpus. Query text is accepted for
    interface parity but not used to filter, the same "no embeddings
    locally" posture ``InMemoryCaseMemoryStore.query`` already has.
    """

    backend_name = "in_memory_fixture"

    async def query(
        self,
        *,
        tenant_id: str,
        corpus: GuidanceCorpus,
        query: str,
        top_k: int = DEFAULT_TOP_K,
    ) -> tuple[list[GuidanceCitation], bool]:
        del query
        manifest = load_guidance_manifest()
        citations: list[GuidanceCitation] = []
        for document in manifest.documents:
            if document.corpus is not corpus:
                continue
            if document.tenant_id is not None and document.tenant_id != tenant_id:
                continue
            citations.append(
                GuidanceCitation(
                    tenant_id=tenant_id,
                    corpus=document.corpus,
                    doc_id=document.doc_id,
                    title=document.title,
                    version=document.version,
                    effective_date=document.effective_date,
                    approved=document.approved,
                    superseded=document.superseded,
                    source_uri=document.source_uri,
                    text=f"{document.title} (fixture excerpt)",
                    similarity_score=0.9,
                )
            )
        return citations[:top_k], True


_INMEMORY_ENV = "COUNTERSIGN_GUIDANCE_INMEMORY"


@functools.cache
def get_guidance_source() -> GuidanceSource:
    """Real Vertex AI RAG Engine by default; explicit opt-out for tests.

    Matches ``get_case_memory_store``'s shape: the in-memory fixture is an
    explicit opt-in via env, never the silent default in a real deployment.
    """
    if os.environ.get(_INMEMORY_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return InMemoryGuidanceSource()
    return VertexRagGuidanceSource()


def reset_guidance_source_cache() -> None:
    get_guidance_source.cache_clear()


def sop_corpus_configured() -> bool:
    """Whether the controlled-SOP corpus is actually configured right now.

    Read directly from env, not from a prior query result, so the
    citation-gate enforcement decision
    (``app/domain/investigation_gate.py::guidance_citation_failures``) is
    based on deployment configuration, never on whether the most recent
    retrieval call happened to succeed.
    """
    if os.environ.get(_INMEMORY_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return True
    return bool(os.environ.get(SOP_CORPUS_ENV, "").strip())
