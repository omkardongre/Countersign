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

"""Durable CFI investigation store and concurrency contract.

The CFI Runtime reaches this store through MCP; the department MCP process
selects the Cloud SQL implementation when the production instance is
configured. The in-memory implementation exists only for explicit local
runs, matching the repository's existing store pattern.

The store exposes two different write operations deliberately:

* ``open`` is an atomic, idempotent insert keyed by
  ``(tenant_id, source_case_id)``. A retried case-graph handoff returns the
  original record rather than opening a duplicate.
* ``save`` is an optimistic-concurrency update keyed by ``cfi_id`` and the
  caller's ``revision``. The store increments the revision and rejects a
  stale writer, preventing two Runtime invocations from silently losing a
  hypothesis/evidence update.

The durable record remains a validated ``CfiInvestigation`` aggregate. The
store never accepts an unvalidated dictionary.
"""

from __future__ import annotations

import functools
from threading import RLock
from typing import Protocol

from app.domain.cfi_models import CfiInvestigation


class CfiConcurrencyError(RuntimeError):
    """A CFI write raced with another writer or changed immutable identity."""


class CfiNotFoundError(KeyError):
    """The requested CFI does not exist for a save operation."""


class CfiStore(Protocol):
    def open(self, investigation: CfiInvestigation) -> CfiInvestigation: ...

    def get(self, cfi_id: str) -> CfiInvestigation | None: ...

    def save(self, investigation: CfiInvestigation) -> CfiInvestigation: ...

    def list_open_for_batch(
        self, *, tenant_id: str, batch_id: str
    ) -> list[CfiInvestigation]: ...

    def reset(self) -> None: ...


class InMemoryCfiStore:
    """Local-only store with the same open/revision semantics as Postgres."""

    def __init__(self) -> None:
        self._rows: dict[str, CfiInvestigation] = {}
        self._lock = RLock()

    @staticmethod
    def _copy(record: CfiInvestigation) -> CfiInvestigation:
        """Keep callers from mutating a stored aggregate out of band."""
        return record.model_copy(deep=True)

    def open(self, investigation: CfiInvestigation) -> CfiInvestigation:
        with self._lock:
            by_id = self._rows.get(investigation.cfi_id)
            if by_id is not None and (
                by_id.tenant_id != investigation.tenant_id
                or by_id.source_case_id != investigation.source_case_id
            ):
                raise CfiConcurrencyError(
                    "CFI id is already bound to a different tenant/source case."
                )
            for existing in self._rows.values():
                if (
                    existing.tenant_id == investigation.tenant_id
                    and existing.source_case_id == investigation.source_case_id
                ):
                    if existing.batch_id != investigation.batch_id:
                        raise CfiConcurrencyError(
                            "The tenant/source case is already bound to a "
                            "different batch."
                        )
                    return self._copy(existing)
            if investigation.revision != 0:
                raise CfiConcurrencyError(
                    "A new CFI must be opened with revision 0."
                )
            stored = investigation.model_copy(deep=True, update={"revision": 0})
            self._rows[stored.cfi_id] = stored
            return self._copy(stored)

    def get(self, cfi_id: str) -> CfiInvestigation | None:
        with self._lock:
            record = self._rows.get(cfi_id)
            return self._copy(record) if record is not None else None

    def save(self, investigation: CfiInvestigation) -> CfiInvestigation:
        with self._lock:
            existing = self._rows.get(investigation.cfi_id)
            if existing is None:
                raise CfiNotFoundError(investigation.cfi_id)
            if (
                existing.tenant_id != investigation.tenant_id
                or existing.source_case_id != investigation.source_case_id
                or existing.batch_id != investigation.batch_id
            ):
                raise CfiConcurrencyError(
                    "CFI tenant, source-case, and batch identity are immutable."
                )
            if existing.revision != investigation.revision:
                raise CfiConcurrencyError(
                    f"Stale CFI revision {investigation.revision}; current is "
                    f"{existing.revision}."
                )
            stored = investigation.model_copy(
                deep=True, update={"revision": existing.revision + 1}
            )
            self._rows[stored.cfi_id] = stored
            return self._copy(stored)

    def list_open_for_batch(
        self, *, tenant_id: str, batch_id: str
    ) -> list[CfiInvestigation]:
        with self._lock:
            return [
                self._copy(record)
                for record in self._rows.values()
                if record.tenant_id == tenant_id
                and record.batch_id == batch_id
                and record.outcome is None
            ]

    def reset(self) -> None:
        with self._lock:
            self._rows.clear()


@functools.cache
def get_cfi_store() -> CfiStore:
    from app.app_utils.cloud_sql import instance_connection_name

    if instance_connection_name():
        from app.domain.cfi_store_postgres import PostgresCfiStore

        return PostgresCfiStore()
    return InMemoryCfiStore()


def reset_cfi_store_cache() -> None:
    get_cfi_store.cache_clear()
