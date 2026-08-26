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

"""Cloud SQL sterility incubation table. Same instance as ADK sessions."""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from contextlib import contextmanager

import pg8000.exceptions

from app.app_utils.cloud_sql import connect_pg8000
from app.domain.sterility_models import (
    SterilityIncubationRecord,
    SterilityIncubationStatus,
    SterilityResumeHandle,
)
from app.domain.sterility_store import (
    RESUME_HANDLE_COLUMNS,
    incubation_hold,
    resume_handle_from_columns,
)

STERILITY_DDL = """
CREATE TABLE IF NOT EXISTS sterility_incubation (
    tenant_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    hold_seconds INTEGER NOT NULL,
    compressed_for_demo BOOLEAN NOT NULL,
    workflow_version TEXT NOT NULL,
    app_name TEXT,
    user_id TEXT,
    session_id TEXT,
    invocation_id TEXT,
    interrupt_id TEXT,
    PRIMARY KEY (tenant_id, batch_id)
)
"""

# Additive so an instance provisioned before the resume handle existed keeps
# its rows instead of needing a drop.
STERILITY_MIGRATIONS = tuple(
    "ALTER TABLE sterility_incubation "
    f"ADD COLUMN IF NOT EXISTS {column} TEXT"
    for column in RESUME_HANDLE_COLUMNS
)

_SELECT_COLUMNS = """
    tenant_id, batch_id, case_id, status, started_at, due_at, completed_at,
    hold_seconds, compressed_for_demo, workflow_version, app_name, user_id,
    session_id, invocation_id, interrupt_id
"""


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@contextmanager
def _connect() -> Iterator:
    conn = connect_pg8000()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_INSUFFICIENT_PRIVILEGE = "42501"


class PostgresSterilityStore:
    def __init__(self) -> None:
        with _connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(STERILITY_DDL)
                for statement in STERILITY_MIGRATIONS:
                    cursor.execute(statement)
            except pg8000.exceptions.DatabaseError as exc:
                if not self._is_missing_ddl_privilege(exc):
                    raise
                conn.rollback()
                self._require_existing_table(cursor)

    @staticmethod
    def _is_missing_ddl_privilege(exc: pg8000.exceptions.DatabaseError) -> bool:
        args = exc.args[0] if exc.args else {}
        return isinstance(args, dict) and args.get("C") == _INSUFFICIENT_PRIVILEGE

    @staticmethod
    def _require_existing_table(cursor) -> None:
        """A caller without DDL rights (e.g. the tick service) must find the
        table already provisioned by an account with schema privileges.
        Missing table here is a real deployment error, not something to
        silently swallow."""
        cursor.execute(
            "SELECT to_regclass('public.sterility_incubation') IS NOT NULL"
        )
        (exists,) = cursor.fetchone()
        if not exists:
            raise RuntimeError(
                "sterility_incubation table does not exist and this "
                "connection lacks privilege to create it."
            )

    def _row(self, raw: tuple) -> SterilityIncubationRecord:
        return SterilityIncubationRecord(
            tenant_id=raw[0],
            batch_id=raw[1],
            case_id=raw[2],
            status=SterilityIncubationStatus(raw[3]),
            started_at=raw[4],
            due_at=raw[5],
            completed_at=raw[6],
            hold_seconds=raw[7],
            compressed_for_demo=bool(raw[8]),
            workflow_version=raw[9],
            resume_handle=resume_handle_from_columns(
                dict(zip(RESUME_HANDLE_COLUMNS, raw[10:15], strict=True))
            ),
            decision_authority="none",
        )

    def get(
        self, tenant_id: str, batch_id: str
    ) -> SterilityIncubationRecord | None:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM sterility_incubation
                WHERE tenant_id = %s AND batch_id = %s
                """,
                (tenant_id, batch_id),
            )
            raw = cursor.fetchone()
        return None if raw is None else self._row(raw)

    def start(
        self,
        *,
        tenant_id: str,
        batch_id: str,
        case_id: str,
        workflow_version: str,
        hold_seconds: int | None = None,
        now: datetime.datetime | None = None,
    ) -> SterilityIncubationRecord:
        existing = self.get(tenant_id, batch_id)
        if existing is not None:
            return existing
        stamp = now or _utcnow()
        hold, compressed = incubation_hold(hold_seconds)
        record = SterilityIncubationRecord(
            tenant_id=tenant_id,
            batch_id=batch_id,
            case_id=case_id,
            status=SterilityIncubationStatus.PENDING,
            started_at=stamp,
            due_at=stamp + hold,
            hold_seconds=int(hold.total_seconds()),
            compressed_for_demo=compressed,
            workflow_version=workflow_version,
            decision_authority="none",
        )
        with _connect() as conn:
            conn.cursor().execute(
                """
                INSERT INTO sterility_incubation (
                    tenant_id, batch_id, case_id, status, started_at, due_at,
                    completed_at, hold_seconds, compressed_for_demo,
                    workflow_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, batch_id) DO NOTHING
                """,
                (
                    record.tenant_id,
                    record.batch_id,
                    record.case_id,
                    record.status.value,
                    record.started_at,
                    record.due_at,
                    None,
                    record.hold_seconds,
                    record.compressed_for_demo,
                    record.workflow_version,
                ),
            )
        stored = self.get(tenant_id, batch_id)
        if stored is None:
            raise RuntimeError(
                f"Incubation insert failed for {tenant_id}/{batch_id}."
            )
        return stored

    def complete(
        self,
        tenant_id: str,
        batch_id: str,
        *,
        now: datetime.datetime | None = None,
    ) -> SterilityIncubationRecord:
        record = self.get(tenant_id, batch_id)
        if record is None:
            raise KeyError(f"No incubation for {tenant_id}/{batch_id}.")
        if record.status is SterilityIncubationStatus.COMPLETE:
            return record
        stamp = now or _utcnow()
        with _connect() as conn:
            conn.cursor().execute(
                """
                UPDATE sterility_incubation
                SET status = %s, completed_at = %s
                WHERE tenant_id = %s AND batch_id = %s
                """,
                (
                    SterilityIncubationStatus.COMPLETE.value,
                    stamp,
                    tenant_id,
                    batch_id,
                ),
            )
        updated = self.get(tenant_id, batch_id)
        if updated is None:
            raise RuntimeError(
                f"Incubation update failed for {tenant_id}/{batch_id}."
            )
        return updated

    def bind_resume_handle(
        self,
        tenant_id: str,
        batch_id: str,
        handle: SterilityResumeHandle,
    ) -> SterilityIncubationRecord:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sterility_incubation
                SET app_name = %s, user_id = %s, session_id = %s,
                    invocation_id = %s, interrupt_id = %s
                WHERE tenant_id = %s AND batch_id = %s
                """,
                (
                    handle.app_name,
                    handle.user_id,
                    handle.session_id,
                    handle.invocation_id,
                    handle.interrupt_id,
                    tenant_id,
                    batch_id,
                ),
            )
            rowcount = cursor.rowcount
        if rowcount == 0:
            raise KeyError(f"No incubation for {tenant_id}/{batch_id}.")
        updated = self.get(tenant_id, batch_id)
        if updated is None:
            raise RuntimeError(
                f"Resume handle write failed for {tenant_id}/{batch_id}."
            )
        return updated

    def tick_due(
        self, *, now: datetime.datetime | None = None
    ) -> list[SterilityIncubationRecord]:
        """Atomic claim via ``UPDATE ... RETURNING``.

        A select-then-update pair would let two overlapping ticks both read
        the same pending row before either completed it, delivering the same
        wake-up twice. The single statement here claims and reads together,
        so Postgres's row-level locking prevents that double delivery.
        """
        stamp = now or _utcnow()
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE sterility_incubation
                SET status = %s, completed_at = %s
                WHERE status = %s AND due_at <= %s
                RETURNING {_SELECT_COLUMNS}
                """,
                (
                    SterilityIncubationStatus.COMPLETE.value,
                    stamp,
                    SterilityIncubationStatus.PENDING.value,
                    stamp,
                ),
            )
            rows = cursor.fetchall()
        return [self._row(raw) for raw in rows]

    def reset(self) -> None:
        with _connect() as conn:
            conn.cursor().execute("DELETE FROM sterility_incubation")
