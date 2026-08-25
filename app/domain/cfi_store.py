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

import datetime
import functools
import os
from threading import RLock
from typing import Protocol

from app.domain.cfi_launch_models import CfiLaunchItem
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

    def count_for_tenant(self, *, tenant_id: str, exclude_cfi_id: str) -> int: ...

    def claim_pending_starts(
        self,
        *,
        now: datetime.datetime,
        lease_seconds: int,
        limit: int,
        max_attempts: int,
    ) -> list[CfiLaunchItem]: ...

    def mark_launch_started(
        self, *, tenant_id: str, cfi_id: str, invocation_id: str
    ) -> CfiLaunchItem: ...

    def mark_launch_failed(
        self, *, tenant_id: str, cfi_id: str, error: str
    ) -> CfiLaunchItem: ...

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

    def count_for_tenant(self, *, tenant_id: str, exclude_cfi_id: str) -> int:
        """Count this tenant's other CFI records for recurrence evidence."""
        with self._lock:
            return sum(
                1
                for record in self._rows.values()
                if record.tenant_id == tenant_id and record.cfi_id != exclude_cfi_id
            )

    def claim_pending_starts(
        self,
        *,
        now: datetime.datetime,
        lease_seconds: int,
        limit: int,
        max_attempts: int,
    ) -> list[CfiLaunchItem]:
        from datetime import timedelta

        from app.domain.cfi_models import CfiLaunchStatus

        with self._lock:
            candidates = sorted(
                self._rows.values(), key=lambda record: (record.opened_at, record.cfi_id)
            )
            claimed: list[CfiLaunchItem] = []
            lease_until = now + timedelta(seconds=lease_seconds)
            for record in candidates:
                if len(claimed) >= limit or record.outcome is not None:
                    continue
                expired = (
                    record.launch_status is CfiLaunchStatus.LAUNCHING
                    and record.launch_lease_until is not None
                    and record.launch_lease_until <= now
                )
                eligible = record.launch_status in {
                    CfiLaunchStatus.PENDING,
                    CfiLaunchStatus.FAILED,
                } or expired
                if not eligible or record.launch_attempts >= max_attempts:
                    continue
                updated = record.model_copy(
                    deep=True,
                    update={
                        "launch_status": CfiLaunchStatus.LAUNCHING,
                        "launch_attempts": record.launch_attempts + 1,
                        "launch_lease_until": lease_until,
                        "launch_error": None,
                    },
                )
                self._rows[record.cfi_id] = updated
                claimed.append(
                    CfiLaunchItem(
                        cfi_id=updated.cfi_id,
                        tenant_id=updated.tenant_id,
                        source_case_id=updated.source_case_id,
                        batch_id=updated.batch_id,
                        launch_attempts=updated.launch_attempts,
                        launch_status=updated.launch_status,
                    )
                )
            return claimed

    def mark_launch_started(
        self, *, tenant_id: str, cfi_id: str, invocation_id: str
    ) -> CfiLaunchItem:
        from app.domain.cfi_models import CfiLaunchStatus

        with self._lock:
            record = self._rows.get(cfi_id)
            if record is None or record.tenant_id != tenant_id:
                raise CfiNotFoundError(cfi_id)
            if record.launch_status is CfiLaunchStatus.STARTED:
                if record.runtime_invocation_id not in {None, invocation_id}:
                    raise CfiConcurrencyError(
                        "CFI launch is already bound to a different invocation."
                    )
                return CfiLaunchItem(
                    cfi_id=record.cfi_id,
                    tenant_id=record.tenant_id,
                    source_case_id=record.source_case_id,
                    batch_id=record.batch_id,
                    launch_attempts=record.launch_attempts,
                    launch_status=record.launch_status,
                )
            if record.launch_status is not CfiLaunchStatus.LAUNCHING:
                raise CfiConcurrencyError(
                    f"CFI {cfi_id!r} is not in a launchable claimed state."
                )
            updated = record.model_copy(
                deep=True,
                update={
                    "launch_status": CfiLaunchStatus.STARTED,
                    "launch_lease_until": None,
                    "launch_error": None,
                    "runtime_invocation_id": invocation_id,
                },
            )
            self._rows[cfi_id] = updated
            return CfiLaunchItem(
                cfi_id=updated.cfi_id,
                tenant_id=updated.tenant_id,
                source_case_id=updated.source_case_id,
                batch_id=updated.batch_id,
                launch_attempts=updated.launch_attempts,
                launch_status=updated.launch_status,
            )

    def mark_launch_failed(
        self, *, tenant_id: str, cfi_id: str, error: str
    ) -> CfiLaunchItem:
        from app.domain.cfi_models import CfiLaunchStatus

        with self._lock:
            record = self._rows.get(cfi_id)
            if record is None or record.tenant_id != tenant_id:
                raise CfiNotFoundError(cfi_id)
            if record.launch_status is CfiLaunchStatus.STARTED:
                return CfiLaunchItem(
                    cfi_id=record.cfi_id,
                    tenant_id=record.tenant_id,
                    source_case_id=record.source_case_id,
                    batch_id=record.batch_id,
                    launch_attempts=record.launch_attempts,
                    launch_status=record.launch_status,
                )
            updated = record.model_copy(
                deep=True,
                update={
                    "launch_status": CfiLaunchStatus.FAILED,
                    "launch_lease_until": None,
                    "launch_error": error[:2000],
                },
            )
            self._rows[cfi_id] = updated
            return CfiLaunchItem(
                cfi_id=updated.cfi_id,
                tenant_id=updated.tenant_id,
                source_case_id=updated.source_case_id,
                batch_id=updated.batch_id,
                launch_attempts=updated.launch_attempts,
                launch_status=updated.launch_status,
            )

    def reset(self) -> None:
        with self._lock:
            self._rows.clear()


CFI_INMEMORY_ENV = "COUNTERSIGN_CFI_INMEMORY"


@functools.cache
def get_cfi_store() -> CfiStore:
    """Select durable Cloud SQL by default; allow memory only explicitly.

    Production must set ``INSTANCE_CONNECTION_NAME`` so a process restart
    cannot discard investigation state. The in-memory implementation is
    available only when ``COUNTERSIGN_CFI_INMEMORY=1`` is explicitly set for
    a local test or an intentionally disposable local graph run.
    """
    from app.app_utils.cloud_sql import instance_connection_name

    if instance_connection_name():
        from app.domain.cfi_store_postgres import PostgresCfiStore

        return PostgresCfiStore()
    if os.environ.get(CFI_INMEMORY_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return InMemoryCfiStore()
    raise RuntimeError(
        "CFI durable store is not configured: set INSTANCE_CONNECTION_NAME "
        "for Cloud SQL, or set COUNTERSIGN_CFI_INMEMORY=1 explicitly for "
        "local tests/disposable runs."
    )


def reset_cfi_store_cache() -> None:
    get_cfi_store.cache_clear()
