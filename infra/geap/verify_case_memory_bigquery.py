#!/usr/bin/env python3
"""Live check: BigQuery Case Memory write, tenant-scoped VECTOR_SEARCH, redact.

Uses application default credentials. Cleans up the probe rows. Fails if
retrieval falls back to recency (query_embedded=False) — that means the
embedding call failed. Also proves cross-tenant isolation live: a
foreign-tenant row that is a closer vector match than the requested tenant's
own row must never appear in the requested tenant's results, which a
post-filtered implementation could get wrong (see the pre-filter caution in
app/domain/case_memory_store_bigquery.py).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

from app.domain.case_memory_store_bigquery import (
    BigQueryCaseMemoryStore,
    case_memory_table_fqn,
)

PROBE_PREFIX = "case-memory-bq-probe"


async def _run() -> None:
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "countersign-505107")
    store = BigQueryCaseMemoryStore()
    suffix = uuid.uuid4().hex[:8]
    aurex_id = f"{PROBE_PREFIX}:aurex:{suffix}"
    other_id = f"{PROBE_PREFIX}:other:{suffix}"
    # Deliberately near-identical wording, so a closer-vector foreign-tenant
    # row is a real risk this probe can actually catch, not a trivial pass.
    query = "column pressure drift during autoclave sterility hold"
    try:
        aurex = await store.write_fact(
            tenant_id="aurex",
            case_id=f"probe-{suffix}",
            batch_id=f"PROBE-{suffix}",
            fact=(
                "Column pressure drifted +18% over run 4 of lot AX-2291 "
                "during the sterility incubation hold."
            ),
            source_uri=f"historian://aurex/pressure_trace/{suffix}",
            confidence=0.91,
            fact_id=aurex_id,
            root_cause_category="machine",
        )
        await store.write_fact(
            tenant_id="helix",
            case_id=f"probe-other-{suffix}",
            batch_id=f"OTHER-{suffix}",
            fact=(
                "Column pressure drift during the sterility incubation hold "
                "on a different tenant's autoclave run."
            ),
            source_uri=f"historian://helix/pressure_trace/{suffix}",
            confidence=0.91,
            fact_id=other_id,
        )
        facts, embedded = await store.query(tenant_id="aurex", query=query, top_k=5)
        if not embedded:
            raise AssertionError(
                "query_embedded=False: VECTOR_SEARCH did not run "
                "(embedding call failed)."
            )
        ids = {fact.fact_id for fact in facts}
        if aurex.fact_id not in ids:
            raise AssertionError(
                f"Aurex probe fact {aurex.fact_id} missing from VECTOR_SEARCH "
                f"result {ids}."
            )
        if other_id in ids:
            raise AssertionError(
                "Cross-tenant helix fact leaked into aurex query. The tenant "
                "pre-filter is not actually constraining the base table."
            )
        matched = next(f for f in facts if f.fact_id == aurex.fact_id)
        if matched.similarity_score is None:
            raise AssertionError("Matched fact has no similarity_score from a real query.")
        if matched.root_cause_category != "machine":
            raise AssertionError(
                f"root_cause_category round-trip failed: {matched.root_cause_category!r}"
            )
        print(
            f"vector query ok (embedded, {len(facts)} hit(s), tenant-filtered, "
            f"similarity={matched.similarity_score:.4f})"
        )

        redacted = await store.redact(
            tenant_id="aurex", fact_id=aurex.fact_id, reason="live probe cleanup"
        )
        assert redacted.redacted is True
        after, _ = await store.query(tenant_id="aurex", query=query, top_k=5)
        if aurex.fact_id in {fact.fact_id for fact in after}:
            raise AssertionError("Redacted fact still returned by query.")
        print(f"redact ok ({aurex.fact_id})")
    finally:
        from google.cloud import bigquery

        table = case_memory_table_fqn()
        client_module = __import__(
            "app.domain.case_memory_store_bigquery", fromlist=["_client"]
        )
        client = client_module._client()
        delete_query = f"DELETE FROM {table} WHERE fact_id IN UNNEST(@fact_ids)"
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter(
                    "fact_ids", "STRING", [aurex_id, other_id]
                )
            ]
        )
        client.query(delete_query, job_config=job_config).result()
        print(f"probe rows deleted ({suffix})")


def main() -> int:
    try:
        asyncio.run(_run())
    except Exception as exc:
        print(f"BigQuery Case Memory live verify failed: {exc}", file=sys.stderr)
        return 1
    print("Case Memory BigQuery live path ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
