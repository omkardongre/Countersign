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

"""Batch-keyed case status store: Cloud SQL, same instance as the sterility
clock and ADK sessions. In-memory fallback only when no Cloud SQL instance
is configured (unit tests / local graph runs), same selection rule as
``sterility_store.get_sterility_store``.

One row per tenant/batch, upserted, not appended: the sponsor liaison needs
"the current status", not a history, and a table that only ever grows would
answer a status query with a scan instead of a point lookup.
"""

from __future__ import annotations

import datetime
import functools
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from app.domain.case_status_models import CaseStatus, CaseStatusRecord


class CaseStatusStore(Protocol):
    def upsert(self, record: CaseStatusRecord) -> CaseStatusRecord: ...

    def get(self, *, tenant_id: str, batch_id: str) -> CaseStatusRecord | None: ...

    def reset(self) -> None: ...


class InMemoryCaseStatusStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], CaseStatusRecord] = {}

    def upsert(self, record: CaseStatusRecord) -> CaseStatusRecord:
        self._rows[(record.tenant_id, record.batch_id)] = record
        return record

    def get(self, *, tenant_id: str, batch_id: str) -> CaseStatusRecord | None:
        return self._rows.get((tenant_id, batch_id))

    def reset(self) -> None:
        self._rows.clear()


_DDL = """
CREATE TABLE IF NOT EXISTS case_status (
    tenant_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL,
    sterility_due_at TIMESTAMPTZ,
    qp_confirmed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, batch_id)
)
"""

_INSUFFICIENT_PRIVILEGE = "42501"


class PostgresCaseStatusStore:
    """Cloud SQL-backed status. Same DDL-privilege fallback as
    ``PostgresSterilityStore``: a caller without CREATE rights (the
    sponsor-liaison read path, which should not need schema privileges at
    all) must find the table already provisioned.
    """

    def __init__(self) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(_DDL)
            except Exception as exc:  # pg8000.exceptions.DatabaseError
                if not self._is_missing_ddl_privilege(exc):
                    raise
                conn.rollback()
                self._require_existing_table(cursor)

    @staticmethod
    def _is_missing_ddl_privilege(exc: Exception) -> bool:
        args = exc.args[0] if exc.args else {}
        return isinstance(args, dict) and args.get("C") == _INSUFFICIENT_PRIVILEGE

    @staticmethod
    def _require_existing_table(cursor) -> None:
        cursor.execute("SELECT to_regclass('public.case_status') IS NOT NULL")
        (exists,) = cursor.fetchone()
        if not exists:
            raise RuntimeError(
                "case_status table does not exist and this connection lacks "
                "privilege to create it."
            )

    @contextmanager
    def _connect(self) -> Iterator:
        from app.app_utils.cloud_sql import connect_pg8000

        conn = connect_pg8000()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert(self, record: CaseStatusRecord) -> CaseStatusRecord:
        with self._connect() as conn:
            conn.cursor().execute(
                """
                INSERT INTO case_status (
                    tenant_id, batch_id, case_id, status,
                    sterility_due_at, qp_confirmed_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, batch_id) DO UPDATE SET
                    case_id = EXCLUDED.case_id,
                    status = EXCLUDED.status,
                    sterility_due_at = EXCLUDED.sterility_due_at,
                    qp_confirmed_at = EXCLUDED.qp_confirmed_at,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    record.tenant_id,
                    record.batch_id,
                    record.case_id,
                    record.status.value,
                    record.sterility_due_at,
                    record.qp_confirmed_at,
                    record.updated_at,
                ),
            )
        return record

    def get(self, *, tenant_id: str, batch_id: str) -> CaseStatusRecord | None:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT tenant_id, batch_id, case_id, status,
                       sterility_due_at, qp_confirmed_at, updated_at
                FROM case_status
                WHERE tenant_id = %s AND batch_id = %s
                """,
                (tenant_id, batch_id),
            )
            raw = cursor.fetchone()
        if raw is None:
            return None
        return CaseStatusRecord(
            tenant_id=raw[0],
            batch_id=raw[1],
            case_id=raw[2],
            status=CaseStatus(raw[3]),
            sterility_due_at=raw[4],
            qp_confirmed_at=raw[5],
            updated_at=raw[6],
        )

    def reset(self) -> None:
        with self._connect() as conn:
            conn.cursor().execute("DELETE FROM case_status")


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@functools.cache
def get_case_status_store() -> CaseStatusStore:
    from app.app_utils.cloud_sql import instance_connection_name

    if instance_connection_name():
        return PostgresCaseStatusStore()
    return InMemoryCaseStatusStore()


def reset_case_status_store_cache() -> None:
    get_case_status_store.cache_clear()
