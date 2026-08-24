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

"""BigQueryCaseMemoryStore: fake BigQuery client, no live network calls.

Asserts the SQL shape that matters for correctness — tenant/redaction
pre-filter placed inside the VECTOR_SEARCH base-table subquery, bound query
parameters rather than string interpolation, idempotent MERGE-by-fact_id,
and the embedding-outage fallback — rather than exercising a real BigQuery
project.
"""

from __future__ import annotations

import datetime

import pytest

from app.domain import case_memory_store_bigquery as bq_mod
from app.domain.case_memory_store import EmbeddingUnavailable
from app.domain.case_memory_store_bigquery import BigQueryCaseMemoryStore


class _FakeRow(dict):
    """Mimics ``google.cloud.bigquery.table.Row``'s mapping-like access."""


class _FakeQueryJob:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = [_FakeRow(row) for row in rows]

    def result(self) -> list[_FakeRow]:
        return self._rows


class _FakeBigQueryClient:
    """Captures every submitted query + job_config; returns canned rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.next_rows: list[dict] = []

    def query(self, query: str, job_config=None):
        self.calls.append((query, job_config))
        return _FakeQueryJob(self.next_rows)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeBigQueryClient:
    client = _FakeBigQueryClient()
    bq_mod._client.cache_clear()
    monkeypatch.setattr(bq_mod, "_client", lambda: client)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "countersign-505107")
    return client


def _param_map(job_config) -> dict[str, object]:
    result: dict[str, object] = {}
    for p in job_config.query_parameters:
        result[p.name] = p.values if hasattr(p, "values") else p.value
    return result


@pytest.mark.asyncio
async def test_write_fact_merges_by_fact_id_with_bound_parameters(
    fake_client: _FakeBigQueryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_embed(text: str, *, task_type: str) -> list[float]:
        assert task_type == "RETRIEVAL_DOCUMENT"
        return [0.1] * 768

    monkeypatch.setattr(bq_mod, "embed_text", _fake_embed)
    store = BigQueryCaseMemoryStore()

    record = await store.write_fact(
        tenant_id="aurex",
        case_id="c1",
        batch_id="AX-2291",
        fact="Blend-speed excursion resolved by recalibrating the mixer.",
        source_uri="case://aurex/c1",
        confidence=0.9,
        fact_id="qp_outcome:aurex:c1",
        root_cause_category="method",
    )

    assert record.fact_id == "qp_outcome:aurex:c1"
    assert record.root_cause_category == "method"
    assert len(fake_client.calls) == 1
    query, job_config = fake_client.calls[0]
    assert "MERGE" in query
    assert "WHEN MATCHED" in query
    assert "WHEN NOT MATCHED" in query
    params = _param_map(job_config)
    # Values are bound parameters, never spliced into the query string.
    assert params["fact_id"] == "qp_outcome:aurex:c1"
    assert params["tenant_id"] == "aurex"
    assert params["root_cause_category"] == "method"
    assert params["embedding"] == [0.1] * 768


@pytest.mark.asyncio
async def test_query_pre_filters_tenant_inside_vector_search_base_table(
    fake_client: _FakeBigQueryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_embed(text: str, *, task_type: str) -> list[float]:
        assert task_type == "RETRIEVAL_QUERY"
        return [0.2] * 768

    monkeypatch.setattr(bq_mod, "embed_text", _fake_embed)
    fake_client.next_rows = [
        {
            "fact_id": "f1",
            "tenant_id": "aurex",
            "case_id": "c1",
            "batch_id": "AX-2291",
            "fact": "Prior mixer excursion.",
            "source_uri": "case://aurex/c1",
            "confidence": 0.9,
            "observed_at": datetime.datetime.now(datetime.UTC),
            "redacted": False,
            "redacted_reason": None,
            "root_cause_category": "method",
            "distance": 0.1,
        }
    ]
    store = BigQueryCaseMemoryStore()

    facts, embedded = await store.query(tenant_id="aurex", query="mixer", top_k=5)

    assert embedded is True
    assert len(facts) == 1
    assert facts[0].tenant_id == "aurex"
    assert facts[0].root_cause_category == "method"
    # 1 - distance, clamped: never fabricated, always derived from the row.
    assert facts[0].similarity_score == pytest.approx(0.9)

    query, job_config = fake_client.calls[0]
    # The tenant/redaction predicate must be inside the base_table_query
    # argument passed to VECTOR_SEARCH (a pre-filter), never appended as a
    # WHERE clause wrapped around the function call (a post-filter that
    # only runs after a global top-k search already completed).
    vector_search_start = query.index("VECTOR_SEARCH")
    where_index = query.index("WHERE tenant_id")
    closing_paren_of_subquery = query.index(")", where_index)
    assert vector_search_start < where_index < closing_paren_of_subquery
    assert "redacted = FALSE" in query
    params = _param_map(job_config)
    assert params["tenant_id"] == "aurex"
    assert params["top_k"] == 5


@pytest.mark.asyncio
async def test_query_falls_back_to_recency_when_embedding_unavailable(
    fake_client: _FakeBigQueryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(text: str, *, task_type: str) -> list[float]:
        raise EmbeddingUnavailable("embedding service unreachable")

    monkeypatch.setattr(bq_mod, "embed_text", _boom)
    fake_client.next_rows = [
        {
            "fact_id": "f1",
            "tenant_id": "aurex",
            "case_id": "c1",
            "batch_id": "AX-2291",
            "fact": "Recent fact.",
            "source_uri": "case://aurex/c1",
            "confidence": 0.9,
            "observed_at": datetime.datetime.now(datetime.UTC),
            "redacted": False,
            "redacted_reason": None,
            "root_cause_category": None,
        }
    ]
    store = BigQueryCaseMemoryStore()

    facts, embedded = await store.query(tenant_id="aurex", query="anything", top_k=5)

    assert embedded is False
    assert len(facts) == 1
    assert facts[0].similarity_score is None
    query, _ = fake_client.calls[0]
    assert "VECTOR_SEARCH" not in query
    assert "ORDER BY observed_at DESC" in query


@pytest.mark.asyncio
async def test_redact_checks_tenant_before_updating(
    fake_client: _FakeBigQueryClient,
) -> None:
    store = BigQueryCaseMemoryStore()
    fake_client.next_rows = [
        {
            "fact_id": "f1",
            "tenant_id": "aurex",
            "case_id": "c1",
            "batch_id": "AX-2291",
            "fact": "A fact.",
            "source_uri": "case://aurex/c1",
            "confidence": 1.0,
            "observed_at": datetime.datetime.now(datetime.UTC),
            "redacted": False,
            "redacted_reason": None,
            "root_cause_category": None,
        }
    ]

    redacted = await store.redact(
        tenant_id="aurex", fact_id="f1", reason="wrong root cause"
    )

    assert redacted.redacted is True
    assert redacted.redacted_reason == "wrong root cause"
    # First call is the existence/tenant SELECT, second is the UPDATE.
    assert len(fake_client.calls) == 2
    update_query, update_config = fake_client.calls[1]
    assert "UPDATE" in update_query
    assert "SET redacted = TRUE" in update_query
    params = _param_map(update_config)
    assert params["fact_id"] == "f1"
    assert params["tenant_id"] == "aurex"


@pytest.mark.asyncio
async def test_redact_missing_fact_raises_key_error(
    fake_client: _FakeBigQueryClient,
) -> None:
    store = BigQueryCaseMemoryStore()
    fake_client.next_rows = []

    with pytest.raises(KeyError):
        await store.redact(tenant_id="aurex", fact_id="no-such-fact", reason="x")


@pytest.mark.asyncio
async def test_latest_fact_for_batch_is_exact_filter_not_vector_search(
    fake_client: _FakeBigQueryClient,
) -> None:
    store = BigQueryCaseMemoryStore()
    fake_client.next_rows = [
        {
            "fact_id": "qp_outcome:aurex:c1",
            "tenant_id": "aurex",
            "case_id": "c1",
            "batch_id": "AX-2291",
            "fact": "Confirmed.",
            "source_uri": "case://aurex/c1",
            "confidence": 1.0,
            "observed_at": datetime.datetime.now(datetime.UTC),
            "redacted": False,
            "redacted_reason": None,
            "root_cause_category": None,
        }
    ]

    fact = await store.latest_fact_for_batch(tenant_id="aurex", batch_id="AX-2291")

    assert fact is not None
    assert fact.fact_id == "qp_outcome:aurex:c1"
    query, _ = fake_client.calls[0]
    assert "VECTOR_SEARCH" not in query
    assert "ORDER BY observed_at DESC" in query
