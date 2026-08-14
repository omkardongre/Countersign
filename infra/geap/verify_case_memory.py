#!/usr/bin/env python3
"""Live check: Firestore Case Memory write, tenant-filtered KNN, redact.

Uses application default credentials. Cleans up the probe documents.
Fails if retrieval falls back to recency (query_embedded=False) — that
means embeddings or the vector index are not actually live.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

from app.domain.case_memory_store import (
    FirestoreCaseMemoryStore,
    reset_case_memory_store_cache,
)

PROBE_PREFIX = "case-memory-probe"


async def _run() -> None:
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "countersign-505107")
    os.environ.pop("COUNTERSIGN_CASE_MEMORY_INMEMORY", None)
    reset_case_memory_store_cache()
    store = FirestoreCaseMemoryStore()
    suffix = uuid.uuid4().hex[:8]
    aurex_id = f"{PROBE_PREFIX}:aurex:{suffix}"
    other_id = f"{PROBE_PREFIX}:other:{suffix}"
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
        )
        await store.write_fact(
            tenant_id="helix",
            case_id=f"probe-other-{suffix}",
            batch_id=f"OTHER-{suffix}",
            fact="Unrelated helix tenant CoA humidity excursion on a different line.",
            source_uri=f"lims://helix/coa/{suffix}",
            confidence=0.8,
            fact_id=other_id,
        )
        facts, embedded = await store.query(
            tenant_id="aurex", query=query, top_k=5
        )
        if not embedded:
            raise AssertionError(
                "query_embedded=False: vector search did not run "
                "(embedding call failed or vector index missing)."
            )
        ids = {fact.fact_id for fact in facts}
        if aurex.fact_id not in ids:
            raise AssertionError(
                f"Aurex probe fact {aurex.fact_id} missing from KNN result {ids}."
            )
        if other_id in ids:
            raise AssertionError("Cross-tenant helix fact leaked into aurex query.")
        print(f"vector query ok (embedded, {len(facts)} hit(s), tenant-filtered)")

        redacted = await store.redact(
            tenant_id="aurex",
            fact_id=aurex.fact_id,
            reason="live probe cleanup",
        )
        assert redacted.redacted is True
        after, _ = await store.query(tenant_id="aurex", query=query, top_k=5)
        if aurex.fact_id in {fact.fact_id for fact in after}:
            raise AssertionError("Redacted fact still returned by query.")
        print(f"redact ok ({aurex.fact_id})")
    finally:
        for fact_id in (aurex_id, other_id):
            await store._collection().document(fact_id).delete()
        print(f"probe documents deleted ({suffix})")


def main() -> int:
    try:
        asyncio.run(_run())
    except Exception as exc:
        print(f"Case Memory live verify failed: {exc}", file=sys.stderr)
        return 1
    print("Case Memory Firestore live path ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
