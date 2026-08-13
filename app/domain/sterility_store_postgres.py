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

from app.app_utils.cloud_sql import connect_pg8000
from app.domain.sterility_models import (
    SterilityIncubationRecord,
    SterilityIncubationStatus,
)
from app.domain.sterility_store import incubation_hold

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
    PRIMARY KEY (tenant_id, batch_id)
)
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


class PostgresSterilityStore:
    def __init__(self) -> None:
        with _connect() as conn:
            conn.cursor().execute(STERILITY_DDL)

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
            decision_authority="none",
        )

    def get(
        self, tenant_id: str, batch_id: str
    ) -> SterilityIncubationRecord | None:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT tenant_id, batch_id, case_id, status, started_at, due_at,
                       completed_at, hold_seconds, compressed_for_demo,
                       workflow_version
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
        now: datetime.datetime | None = None,
    ) -> SterilityIncubationRecord:
        existing = self.get(tenant_id, batch_id)
        if existing is not None:
            return existing
        stamp = now or _utcnow()
        hold, compressed = incubation_hold()
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

    def tick_due(
        self, *, now: datetime.datetime | None = None
    ) -> list[SterilityIncubationRecord]:
        stamp = now or _utcnow()
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT tenant_id, batch_id
                FROM sterility_incubation
                WHERE status = %s AND due_at <= %s
                """,
                (SterilityIncubationStatus.PENDING.value, stamp),
            )
            rows = cursor.fetchall()
        return [self.complete(raw[0], raw[1], now=stamp) for raw in rows]

    def reset(self) -> None:
        with _connect() as conn:
            conn.cursor().execute("DELETE FROM sterility_incubation")
