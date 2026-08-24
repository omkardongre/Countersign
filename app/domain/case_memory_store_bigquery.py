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

"""Case Memory on BigQuery: tenant-scoped ``VECTOR_SEARCH``, real rows.

Doc 12 T1-4: prior-deviation similarity search backed by a real BigQuery
table and a real ``VECTOR_SEARCH`` call, replacing the Firestore KNN
implementation for deployments that opt in via
``COUNTERSIGN_CASE_MEMORY_BACKEND=bigquery`` (``app/domain/
case_memory_store.py::get_case_memory_store``). Implements the same
``CaseMemoryStore`` protocol, so the MCP tools (``prior_art_query``/
``prior_art_write``/``prior_art_redact``), the Runtime client
(``McpCaseMemoryStore``), and the Investigation agent's citation flow do not
change at all — only where a fact is stored and how "similar" is computed.

Tenant isolation is enforced as a **pre-filter inside the base-table
subquery** passed to ``VECTOR_SEARCH``, per Google's own filtering guidance
(cloud.google.com/bigquery/docs/vector-index#pre-filters-and-post-filters):
a global top-k search followed by a tenant ``WHERE`` clause outside the
function is a post-filter, and can silently return fewer than ``top_k`` rows
or let a different tenant's closer vectors starve the requested tenant's own
results out of the candidate set entirely. ``tenant_id`` is declared a
*stored* column on the vector index specifically so this pre-filter can use
the index rather than degrading to a full-table scan
(cloud.google.com/bigquery/docs/vector-index#pre-filter-with-stored-columns).

Writes are an idempotent ``MERGE`` keyed by ``fact_id``, matching the same
"a rerun overwrites, never duplicates" contract
``FirestoreCaseMemoryStore.write_fact`` already has via Firestore's
``set()``. Redaction is a targeted ``UPDATE``, never a physical delete, so a
poisoned fact's row (and its audit trail) still exists — the same posture
``FirestoreCaseMemoryStore.redact`` already has.
"""

from __future__ import annotations

import functools
import os
import re
from typing import Any

from app.domain.case_memory_models import CaseMemoryFact
from app.domain.case_memory_store import (
    DEFAULT_TOP_K,
    EMBED_TASK_TYPE_DOCUMENT,
    EMBED_TASK_TYPE_QUERY,
    EmbeddingUnavailable,
    embed_text,
)

CASE_MEMORY_DATASET_ENV = "COUNTERSIGN_CASE_MEMORY_BQ_DATASET"
CASE_MEMORY_TABLE_ENV = "COUNTERSIGN_CASE_MEMORY_BQ_TABLE"
DEFAULT_DATASET = "countersign_case_memory"
DEFAULT_TABLE = "case_memory_facts"

_VECTOR_INDEX_NAME = "case_memory_facts_embedding_idx"

# SQL identifiers cannot be bound as query parameters (same reasoning as
# app/domain/department_source.py::require_identifier). Dataset/table names
# come from deployment env, not end-user input, but an unvalidated splice
# into a query string is an injection sink regardless of who sets it.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _require_identifier(value: str, *, label: str) -> str:
    if not _IDENTIFIER.match(value):
        raise RuntimeError(f"{label} {value!r} is not a valid SQL identifier.")
    return value


def case_memory_project_id() -> str:
    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        or os.environ.get("PROJECT_ID", "").strip()
    )
    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT (or PROJECT_ID) must be set to read Case "
            "Memory from BigQuery."
        )
    return project


def case_memory_dataset_name() -> str:
    return _require_identifier(
        os.environ.get(CASE_MEMORY_DATASET_ENV, "").strip() or DEFAULT_DATASET,
        label=CASE_MEMORY_DATASET_ENV,
    )


def case_memory_table_name() -> str:
    return _require_identifier(
        os.environ.get(CASE_MEMORY_TABLE_ENV, "").strip() or DEFAULT_TABLE,
        label=CASE_MEMORY_TABLE_ENV,
    )


def case_memory_table_fqn() -> str:
    return (
        f"{case_memory_project_id()}."
        f"{case_memory_dataset_name()}.{case_memory_table_name()}"
    )


@functools.cache
def _client():
    from google.cloud import bigquery

    return bigquery.Client(project=case_memory_project_id())


# DDL for operators/provisioning scripts. The base table is the source of
# truth (per the schema-drift caution in the T1-4 research: keep the durable
# row canonical and treat the vector index as an accelerator, not a second
# store); the vector index is created separately with ``CREATE VECTOR
# INDEX`` once the table holds enough rows (BigQuery does not populate an
# index on tables smaller than 10 MB, and this is provisioning-time DDL, not
# something this module runs itself on every write).
CASE_MEMORY_FACTS_DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {table} (
    fact_id STRING NOT NULL,
    tenant_id STRING NOT NULL,
    case_id STRING NOT NULL,
    batch_id STRING NOT NULL,
    fact STRING NOT NULL,
    source_uri STRING NOT NULL,
    confidence FLOAT64 NOT NULL,
    observed_at TIMESTAMP NOT NULL,
    redacted BOOL NOT NULL,
    redacted_reason STRING,
    root_cause_category STRING,
    embedding ARRAY<FLOAT64>
)
"""

CASE_MEMORY_FACTS_VECTOR_INDEX_DDL_TEMPLATE = """
CREATE VECTOR INDEX IF NOT EXISTS {index_name}
ON {table}(embedding)
STORING(tenant_id, redacted)
OPTIONS(index_type = 'IVF', distance_type = 'COSINE')
"""


def _row_to_fact(row: Any, *, similarity_score: float | None = None) -> CaseMemoryFact:
    return CaseMemoryFact(
        fact_id=row["fact_id"],
        tenant_id=row["tenant_id"],
        case_id=row["case_id"],
        batch_id=row["batch_id"],
        fact=row["fact"],
        source_uri=row["source_uri"],
        confidence=float(row["confidence"]),
        observed_at=row["observed_at"],
        redacted=bool(row["redacted"]),
        redacted_reason=row.get("redacted_reason"),
        root_cause_category=row.get("root_cause_category"),
        similarity_score=similarity_score,
    )


class BigQueryCaseMemoryStore:
    """BigQuery-backed Case Memory: real ``VECTOR_SEARCH``, tenant pre-filtered."""

    backend_name = "bigquery"

    def source_uri(self, *, tenant_id: str, fact_id: str) -> str:
        return f"bq://{case_memory_table_fqn()}?tenant_id={tenant_id}&fact_id={fact_id}"

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
        root_cause_category: str | None = None,
    ) -> CaseMemoryFact:
        """Idempotent by ``fact_id`` via ``MERGE``: a resume-triggered rerun
        with the same ``fact_id`` overwrites this row rather than inserting
        a duplicate, matching ``FirestoreCaseMemoryStore.write_fact``'s
        ``set()`` semantics.

        The embedding is generated with ``RETRIEVAL_DOCUMENT`` task type
        (this row is a stored document to be found later, not a query), and
        is required, not best-effort: unlike the Firestore store's "still
        write without a vector" fallback, a BigQuery row with a ``NULL``
        embedding is invisible to ``VECTOR_SEARCH`` entirely (the function
        skips rows with missing embeddings on an autonomous-embedding
        column, and a manually-populated ``NULL`` array element is rejected
        by the vector index's own non-NULL requirement), so a silently
        unembedded fact here would be a Case Memory fact that can never be
        found by similarity, with no visible symptom until someone notices
        it is missing from every query. Failing the write instead makes
        that failure visible at the point it happens.
        """
        import datetime
        import uuid

        from google.cloud import bigquery

        resolved_fact_id = fact_id or str(uuid.uuid4())
        vector = await embed_text(fact, task_type=EMBED_TASK_TYPE_DOCUMENT)
        observed_at = datetime.datetime.now(datetime.UTC)
        table = case_memory_table_fqn()

        query = f"""
        MERGE {table} AS target
        USING (
          SELECT
            @fact_id AS fact_id,
            @tenant_id AS tenant_id,
            @case_id AS case_id,
            @batch_id AS batch_id,
            @fact AS fact,
            @source_uri AS source_uri,
            @confidence AS confidence,
            @observed_at AS observed_at,
            FALSE AS redacted,
            CAST(NULL AS STRING) AS redacted_reason,
            @root_cause_category AS root_cause_category,
            @embedding AS embedding
        ) AS source
        ON target.fact_id = source.fact_id
        WHEN MATCHED THEN
          UPDATE SET
            tenant_id = source.tenant_id,
            case_id = source.case_id,
            batch_id = source.batch_id,
            fact = source.fact,
            source_uri = source.source_uri,
            confidence = source.confidence,
            observed_at = source.observed_at,
            redacted = source.redacted,
            redacted_reason = source.redacted_reason,
            root_cause_category = source.root_cause_category,
            embedding = source.embedding
        WHEN NOT MATCHED THEN
          INSERT (
            fact_id, tenant_id, case_id, batch_id, fact, source_uri,
            confidence, observed_at, redacted, redacted_reason,
            root_cause_category, embedding
          )
          VALUES (
            source.fact_id, source.tenant_id, source.case_id,
            source.batch_id, source.fact, source.source_uri,
            source.confidence, source.observed_at, source.redacted,
            source.redacted_reason, source.root_cause_category,
            source.embedding
          )
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("fact_id", "STRING", resolved_fact_id),
                bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
                bigquery.ScalarQueryParameter("case_id", "STRING", case_id),
                bigquery.ScalarQueryParameter("batch_id", "STRING", batch_id),
                bigquery.ScalarQueryParameter("fact", "STRING", fact),
                bigquery.ScalarQueryParameter("source_uri", "STRING", source_uri),
                bigquery.ScalarQueryParameter("confidence", "FLOAT64", confidence),
                bigquery.ScalarQueryParameter(
                    "observed_at", "TIMESTAMP", observed_at
                ),
                bigquery.ScalarQueryParameter(
                    "root_cause_category", "STRING", root_cause_category
                ),
                bigquery.ArrayQueryParameter("embedding", "FLOAT64", vector),
            ]
        )
        await _run_query(query, job_config)
        return CaseMemoryFact(
            fact_id=resolved_fact_id,
            tenant_id=tenant_id,
            case_id=case_id,
            batch_id=batch_id,
            fact=fact,
            source_uri=source_uri,
            confidence=confidence,
            observed_at=observed_at,
            redacted=False,
            root_cause_category=root_cause_category,
        )

    async def query(
        self, *, tenant_id: str, query: str, top_k: int = DEFAULT_TOP_K
    ) -> tuple[list[CaseMemoryFact], bool]:
        """Tenant-scoped ``VECTOR_SEARCH``. Falls back to tenant-scoped
        recency only if the embedding call itself fails — never on a
        BigQuery query failure, which is a real infrastructure error the
        caller (``resolve_prior_art``) already treats as advisory and logs,
        not something this method should mask by pretending to be a clean
        "no embeddings" fallback.

        The tenant and ``redacted = FALSE`` predicates sit in the
        ``base_table_query`` argument passed to ``VECTOR_SEARCH`` — the
        pre-filter position per Google's own filtering guidance — rather
        than in a ``WHERE`` clause wrapped around the function call, which
        would be a post-filter applied only after the global top-k search
        already ran and could starve this tenant's own rows out of the
        candidate set.
        """
        from google.cloud import bigquery

        try:
            vector = await embed_text(query, task_type=EMBED_TASK_TYPE_QUERY)
        except EmbeddingUnavailable:
            facts = await self._recency_fallback(tenant_id=tenant_id, top_k=top_k)
            return facts, False

        table = case_memory_table_fqn()
        search_query = f"""
        SELECT
          base.fact_id, base.tenant_id, base.case_id, base.batch_id,
          base.fact, base.source_uri, base.confidence, base.observed_at,
          base.redacted, base.redacted_reason, base.root_cause_category,
          distance
        FROM VECTOR_SEARCH(
          (
            SELECT *
            FROM {table}
            WHERE tenant_id = @tenant_id AND redacted = FALSE
          ),
          'embedding',
          (SELECT @query_embedding AS embedding),
          'embedding',
          top_k => @top_k,
          distance_type => 'COSINE'
        )
        ORDER BY distance ASC
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
                bigquery.ArrayQueryParameter("query_embedding", "FLOAT64", vector),
                bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
            ]
        )
        rows = await _run_query(search_query, job_config)
        facts = [
            _row_to_fact(
                row,
                # COSINE distance in [0, 2]; 1 - distance is the matching
                # similarity convention BigQuery's own tutorial recall
                # comparison implies (closer to 1 is more similar), reported
                # verbatim from the real query result, never estimated.
                similarity_score=max(0.0, min(1.0, 1.0 - float(row["distance"]))),
            )
            for row in rows
        ]
        return facts, True

    async def _recency_fallback(
        self, *, tenant_id: str, top_k: int
    ) -> list[CaseMemoryFact]:
        from google.cloud import bigquery

        table = case_memory_table_fqn()
        recency_query = f"""
        SELECT fact_id, tenant_id, case_id, batch_id, fact, source_uri,
               confidence, observed_at, redacted, redacted_reason,
               root_cause_category
        FROM {table}
        WHERE tenant_id = @tenant_id AND redacted = FALSE
        ORDER BY observed_at DESC
        LIMIT @top_k
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
                bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
            ]
        )
        rows = await _run_query(recency_query, job_config)
        return [_row_to_fact(row) for row in rows]

    async def redact(
        self, *, tenant_id: str, fact_id: str, reason: str
    ) -> CaseMemoryFact:
        """Targeted ``UPDATE``, tenant-checked, never a physical delete —
        matching ``FirestoreCaseMemoryStore.redact``'s posture that a
        redacted fact's row (and audit trail) still exists.
        """
        from google.cloud import bigquery

        table = case_memory_table_fqn()
        existing = await self._get_fact(tenant_id=tenant_id, fact_id=fact_id)
        if existing is None:
            raise KeyError(f"No Case Memory fact {fact_id!r} for tenant {tenant_id!r}.")

        update_query = f"""
        UPDATE {table}
        SET redacted = TRUE, redacted_reason = @reason
        WHERE fact_id = @fact_id AND tenant_id = @tenant_id
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("fact_id", "STRING", fact_id),
                bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
                bigquery.ScalarQueryParameter("reason", "STRING", reason),
            ]
        )
        await _run_query(update_query, job_config)
        return existing.model_copy(update={"redacted": True, "redacted_reason": reason})

    async def _get_fact(
        self, *, tenant_id: str, fact_id: str
    ) -> CaseMemoryFact | None:
        from google.cloud import bigquery

        table = case_memory_table_fqn()
        select_query = f"""
        SELECT fact_id, tenant_id, case_id, batch_id, fact, source_uri,
               confidence, observed_at, redacted, redacted_reason,
               root_cause_category
        FROM {table}
        WHERE fact_id = @fact_id AND tenant_id = @tenant_id
        LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("fact_id", "STRING", fact_id),
                bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
            ]
        )
        rows = await _run_query(select_query, job_config)
        if not rows:
            return None
        return _row_to_fact(rows[0])

    async def latest_fact_for_batch(
        self, *, tenant_id: str, batch_id: str
    ) -> CaseMemoryFact | None:
        """Exact tenant+batch filter, most recent non-redacted row. No
        vector search — same contract as
        ``FirestoreCaseMemoryStore.latest_fact_for_batch``.
        """
        from google.cloud import bigquery

        table = case_memory_table_fqn()
        select_query = f"""
        SELECT fact_id, tenant_id, case_id, batch_id, fact, source_uri,
               confidence, observed_at, redacted, redacted_reason,
               root_cause_category
        FROM {table}
        WHERE tenant_id = @tenant_id AND batch_id = @batch_id
          AND redacted = FALSE
        ORDER BY observed_at DESC
        LIMIT 1
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
                bigquery.ScalarQueryParameter("batch_id", "STRING", batch_id),
            ]
        )
        rows = await _run_query(select_query, job_config)
        if not rows:
            return None
        return _row_to_fact(rows[0])


async def _run_query(query: str, job_config: Any) -> list[Any]:
    """Runs one BigQuery query job off the event loop.

    ``google.cloud.bigquery.Client`` is a synchronous, blocking client (no
    native asyncio support), while every MCP tool handler in this process is
    async. Blocking the event loop for the duration of a query job would
    stall every other in-flight MCP request on this connection, so the job
    submission and result fetch both run in the default thread-pool executor
    instead — the same "don't block async on a sync client" shape recommended
    for using blocking clients from async code.
    """
    import asyncio

    loop = asyncio.get_running_loop()

    def _execute() -> list[Any]:
        return list(_client().query(query, job_config=job_config).result())

    return await loop.run_in_executor(None, _execute)
