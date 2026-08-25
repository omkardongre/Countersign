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

"""Durable CFI interview store and concurrency contract.

Same two-operation shape as ``cfi_store.py``:

* ``open`` is an atomic, idempotent insert keyed by
  ``(tenant_id, cfi_id, hypothesis_id, round_number)``. A retried interview
  start returns the existing record rather than opening a duplicate.
* ``save`` is an optimistic-concurrency update keyed by ``interview_id`` and
  the caller's ``revision``.

The CFI Runtime reaches this store through MCP (``cfi_interview_store_mcp.py``);
the department MCP process selects the Cloud SQL implementation when the
production instance is configured.
"""

from __future__ import annotations

import functools
import os
from threading import RLock
from typing import Protocol

from app.domain.cfi_interview_models import CfiInterviewRecord


class CfiInterviewConcurrencyError(RuntimeError):
    """An interview write raced with another writer or changed identity."""


class CfiInterviewNotFoundError(KeyError):
    """The requested interview does not exist for a save operation."""


class CfiInterviewStore(Protocol):
    def open(self, record: CfiInterviewRecord) -> CfiInterviewRecord: ...

    def get(self, interview_id: str) -> CfiInterviewRecord | None: ...

    def save(self, record: CfiInterviewRecord) -> CfiInterviewRecord: ...

    def list_waiting(self) -> list[CfiInterviewRecord]: ...

    def claim_waiting(
        self, *, now, lease_seconds: int, limit: int = 100
    ) -> list[CfiInterviewRecord]: ...

    def release_poll(self, interview_id: str) -> CfiInterviewRecord | None: ...

    def reset(self) -> None: ...


class InMemoryCfiInterviewStore:
    """Local-only store with the same open/revision semantics as Postgres."""

    def __init__(self) -> None:
        self._rows: dict[str, CfiInterviewRecord] = {}
        self._lock = RLock()

    @staticmethod
    def _copy(record: CfiInterviewRecord) -> CfiInterviewRecord:
        return record.model_copy(deep=True)

    def open(self, record: CfiInterviewRecord) -> CfiInterviewRecord:
        with self._lock:
            for existing in self._rows.values():
                if (
                    existing.tenant_id == record.tenant_id
                    and existing.cfi_id == record.cfi_id
                    and existing.hypothesis_id == record.hypothesis_id
                    and existing.round_number == record.round_number
                ):
                    return self._copy(existing)
            if record.revision != 0:
                raise CfiInterviewConcurrencyError(
                    "A new interview must be opened with revision 0."
                )
            stored = record.model_copy(deep=True, update={"revision": 0})
            self._rows[stored.interview_id] = stored
            return self._copy(stored)

    def get(self, interview_id: str) -> CfiInterviewRecord | None:
        with self._lock:
            record = self._rows.get(interview_id)
            return self._copy(record) if record is not None else None

    def save(self, record: CfiInterviewRecord) -> CfiInterviewRecord:
        with self._lock:
            existing = self._rows.get(record.interview_id)
            if existing is None:
                raise CfiInterviewNotFoundError(record.interview_id)
            if (
                existing.tenant_id != record.tenant_id
                or existing.cfi_id != record.cfi_id
                or existing.hypothesis_id != record.hypothesis_id
                or existing.round_number != record.round_number
            ):
                raise CfiInterviewConcurrencyError(
                    "Interview tenant, CFI, hypothesis, and round identity are immutable."
                )
            if existing.revision != record.revision:
                raise CfiInterviewConcurrencyError(
                    f"Stale interview revision {record.revision}; current is "
                    f"{existing.revision}."
                )
            stored = record.model_copy(
                deep=True, update={"revision": existing.revision + 1}
            )
            self._rows[stored.interview_id] = stored
            return self._copy(stored)

    def list_waiting(self) -> list[CfiInterviewRecord]:
        with self._lock:
            from app.domain.cfi_interview_models import CfiInterviewStatus

            return [
                self._copy(record)
                for record in self._rows.values()
                if record.status is CfiInterviewStatus.WAITING_FOR_REPLY
            ]

    def claim_waiting(
        self, *, now, lease_seconds: int, limit: int = 100
    ) -> list[CfiInterviewRecord]:
        with self._lock:
            from datetime import timedelta

            from app.domain.cfi_interview_models import CfiInterviewStatus

            lease_until = now + timedelta(seconds=lease_seconds)
            candidates = sorted(
                self._rows.values(),
                key=lambda record: (record.created_at, record.interview_id),
            )
            claimed: list[CfiInterviewRecord] = []
            for record in candidates:
                if len(claimed) >= limit:
                    break
                eligible = record.status in {
                    CfiInterviewStatus.WAITING_FOR_REPLY,
                    CfiInterviewStatus.REPLY_CAPTURED,
                    CfiInterviewStatus.INTERVIEW_UNANSWERED,
                }
                expired = (
                    record.status is CfiInterviewStatus.POLLING
                    and record.poll_lease_until is not None
                    and record.poll_lease_until <= now
                )
                if not eligible and not expired:
                    continue
                updated = record.model_copy(
                    update={
                        "status": CfiInterviewStatus.POLLING,
                        "poll_lease_until": lease_until,
                    }
                )
                self._rows[record.interview_id] = updated
                claimed.append(self._copy(updated))
            return claimed

    def release_poll(self, interview_id: str) -> CfiInterviewRecord | None:
        with self._lock:
            record = self._rows.get(interview_id)
            if record is None:
                return None
            from app.domain.cfi_interview_models import CfiInterviewStatus

            if record.status is not CfiInterviewStatus.POLLING:
                return self._copy(record)
            updated = record.model_copy(
                update={
                    "status": CfiInterviewStatus.WAITING_FOR_REPLY,
                    "poll_lease_until": None,
                }
            )
            self._rows[interview_id] = updated
            return self._copy(updated)

    def reset(self) -> None:
        with self._lock:
            self._rows.clear()


@functools.cache
def get_cfi_interview_store() -> CfiInterviewStore:
    from app.app_utils.cloud_sql import instance_connection_name

    # In-memory state is an explicit unit-test/local opt-in only. A missing
    # Cloud SQL configuration must never silently turn a production interview
    # into process-local state that disappears on restart.
    if os.environ.get("COUNTERSIGN_CFI_INTERVIEW_INMEMORY", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return InMemoryCfiInterviewStore()
    if not instance_connection_name():
        raise RuntimeError(
            "CFI interview store is not configured: set "
            "INSTANCE_CONNECTION_NAME for Cloud SQL, or explicitly set "
            "COUNTERSIGN_CFI_INTERVIEW_INMEMORY=1 only for local tests."
        )
    from app.domain.cfi_interview_store_postgres import PostgresCfiInterviewStore

    return PostgresCfiInterviewStore()


def reset_cfi_interview_store_cache() -> None:
    get_cfi_interview_store.cache_clear()
