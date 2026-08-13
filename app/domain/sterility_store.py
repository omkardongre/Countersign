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

"""Durable sterility incubation store. Not session memory and not an LLM."""

from __future__ import annotations

import datetime
import functools
import os
import sqlite3
from typing import Protocol

from app.domain.sterility_models import (
    SterilityIncubationRecord,
    SterilityIncubationStatus,
)

DEFAULT_HOLD = datetime.timedelta(days=14)
HOLD_SECONDS_ENV = "COUNTERSIGN_STERILITY_HOLD_SECONDS"
STORE_PATH_ENV = "COUNTERSIGN_STERILITY_DB"


def incubation_hold() -> tuple[datetime.timedelta, bool]:
    """Return (hold, compressed_for_demo). Demo may shorten 14 days; label it."""
    raw = os.environ.get(HOLD_SECONDS_ENV)
    if raw is None or raw == "":
        return DEFAULT_HOLD, False
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{HOLD_SECONDS_ENV} must be a positive integer of seconds."
        ) from exc
    if seconds <= 0:
        raise ValueError(f"{HOLD_SECONDS_ENV} must be > 0, not {seconds}.")
    return datetime.timedelta(seconds=seconds), True


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class SterilityStore(Protocol):
    def get(
        self, tenant_id: str, batch_id: str
    ) -> SterilityIncubationRecord | None: ...

    def start(
        self,
        *,
        tenant_id: str,
        batch_id: str,
        case_id: str,
        workflow_version: str,
        now: datetime.datetime | None = None,
    ) -> SterilityIncubationRecord: ...

    def complete(
        self,
        tenant_id: str,
        batch_id: str,
        *,
        now: datetime.datetime | None = None,
    ) -> SterilityIncubationRecord: ...

    def tick_due(
        self, *, now: datetime.datetime | None = None
    ) -> list[SterilityIncubationRecord]: ...

    def reset(self) -> None: ...


class InMemorySterilityStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], SterilityIncubationRecord] = {}

    def get(
        self, tenant_id: str, batch_id: str
    ) -> SterilityIncubationRecord | None:
        return self._rows.get((tenant_id, batch_id))

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
        self._rows[(tenant_id, batch_id)] = record
        return record

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
        updated = record.model_copy(
            update={
                "status": SterilityIncubationStatus.COMPLETE,
                "completed_at": stamp,
            }
        )
        self._rows[(tenant_id, batch_id)] = updated
        return updated

    def tick_due(
        self, *, now: datetime.datetime | None = None
    ) -> list[SterilityIncubationRecord]:
        stamp = now or _utcnow()
        done: list[SterilityIncubationRecord] = []
        for record in list(self._rows.values()):
            if (
                record.status is SterilityIncubationStatus.PENDING
                and stamp >= record.due_at
            ):
                done.append(
                    self.complete(
                        record.tenant_id, record.batch_id, now=stamp
                    )
                )
        return done

    def reset(self) -> None:
        self._rows.clear()


class SqliteSterilityStore:
    """SQLite table the demo can show next to ADK session rows."""

    def __init__(self, path: str) -> None:
        self._path = path
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS sterility_incubation (
                    tenant_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    completed_at TEXT,
                    hold_seconds INTEGER NOT NULL,
                    compressed_for_demo INTEGER NOT NULL,
                    workflow_version TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, batch_id)
                )
                """
            )
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path)
        db.row_factory = sqlite3.Row
        return db

    def _row(self, raw: sqlite3.Row) -> SterilityIncubationRecord:
        return SterilityIncubationRecord(
            tenant_id=raw["tenant_id"],
            batch_id=raw["batch_id"],
            case_id=raw["case_id"],
            status=SterilityIncubationStatus(raw["status"]),
            started_at=datetime.datetime.fromisoformat(raw["started_at"]),
            due_at=datetime.datetime.fromisoformat(raw["due_at"]),
            completed_at=(
                datetime.datetime.fromisoformat(raw["completed_at"])
                if raw["completed_at"]
                else None
            ),
            hold_seconds=raw["hold_seconds"],
            compressed_for_demo=bool(raw["compressed_for_demo"]),
            workflow_version=raw["workflow_version"],
            decision_authority="none",
        )

    def get(
        self, tenant_id: str, batch_id: str
    ) -> SterilityIncubationRecord | None:
        with self._connect() as db:
            raw = db.execute(
                """
                SELECT * FROM sterility_incubation
                WHERE tenant_id = ? AND batch_id = ?
                """,
                (tenant_id, batch_id),
            ).fetchone()
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
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO sterility_incubation (
                    tenant_id, batch_id, case_id, status, started_at, due_at,
                    completed_at, hold_seconds, compressed_for_demo,
                    workflow_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.tenant_id,
                    record.batch_id,
                    record.case_id,
                    record.status.value,
                    record.started_at.isoformat(),
                    record.due_at.isoformat(),
                    None,
                    record.hold_seconds,
                    int(record.compressed_for_demo),
                    record.workflow_version,
                ),
            )
            db.commit()
        return record

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
        with self._connect() as db:
            db.execute(
                """
                UPDATE sterility_incubation
                SET status = ?, completed_at = ?
                WHERE tenant_id = ? AND batch_id = ?
                """,
                (
                    SterilityIncubationStatus.COMPLETE.value,
                    stamp.isoformat(),
                    tenant_id,
                    batch_id,
                ),
            )
            db.commit()
        updated = self.get(tenant_id, batch_id)
        assert updated is not None
        return updated

    def tick_due(
        self, *, now: datetime.datetime | None = None
    ) -> list[SterilityIncubationRecord]:
        stamp = now or _utcnow()
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT tenant_id, batch_id, due_at, status
                FROM sterility_incubation
                WHERE status = ? AND due_at <= ?
                """,
                (
                    SterilityIncubationStatus.PENDING.value,
                    stamp.isoformat(),
                ),
            ).fetchall()
        return [
            self.complete(raw["tenant_id"], raw["batch_id"], now=stamp)
            for raw in rows
        ]

    def reset(self) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM sterility_incubation")
            db.commit()


@functools.cache
def get_sterility_store() -> SterilityStore:
    path = os.environ.get(STORE_PATH_ENV)
    if path:
        return SqliteSterilityStore(path)
    from app.app_utils.cloud_sql import instance_connection_name

    if instance_connection_name():
        from app.domain.sterility_store_postgres import PostgresSterilityStore

        return PostgresSterilityStore()
    return InMemorySterilityStore()


def reset_sterility_store_cache() -> None:
    get_sterility_store.cache_clear()
