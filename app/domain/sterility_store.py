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
    SterilityResumeHandle,
)

HOLD_SECONDS_ENV = "COUNTERSIGN_STERILITY_HOLD_SECONDS"
STORE_PATH_ENV = "COUNTERSIGN_STERILITY_DB"


def incubation_hold(
    case_hold_seconds: int | None = None,
) -> tuple[datetime.timedelta, bool]:
    """Return (hold, compressed_for_demo).

    There is no platform-wide default: pharmacopeia sterility test durations
    are product-specific, and QA sets the real duration at intake
    (``CaseIntake.sterility_hold_days``). ``COUNTERSIGN_STERILITY_HOLD_SECONDS``
    always wins when set — it exists only to compress a local/dev run, and
    must still be able to force-shorten even a QA-specified duration.
    Otherwise ``case_hold_seconds`` is required.
    """
    raw = os.environ.get(HOLD_SECONDS_ENV)
    if raw is not None and raw != "":
        try:
            seconds = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"{HOLD_SECONDS_ENV} must be a positive integer of seconds."
            ) from exc
        if seconds <= 0:
            raise ValueError(f"{HOLD_SECONDS_ENV} must be > 0, not {seconds}.")
        return datetime.timedelta(seconds=seconds), True
    if case_hold_seconds is None:
        raise ValueError(
            "No sterility hold duration was provided. QA must set "
            "CaseIntake.sterility_hold_days at intake; there is no "
            f"platform default. (Set {HOLD_SECONDS_ENV} only to compress a "
            "local/dev run.)"
        )
    if case_hold_seconds <= 0:
        raise ValueError(f"case_hold_seconds must be > 0, not {case_hold_seconds}.")
    return datetime.timedelta(seconds=case_hold_seconds), False


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


RESUME_HANDLE_COLUMNS = (
    "app_name",
    "user_id",
    "session_id",
    "invocation_id",
    "interrupt_id",
)


def resume_handle_from_columns(
    values: dict[str, str | None],
) -> SterilityResumeHandle | None:
    """Rebuild the handle only when every column is present."""
    if any(not values.get(column) for column in RESUME_HANDLE_COLUMNS):
        return None
    return SterilityResumeHandle(
        app_name=str(values["app_name"]),
        user_id=str(values["user_id"]),
        session_id=str(values["session_id"]),
        invocation_id=str(values["invocation_id"]),
        interrupt_id=str(values["interrupt_id"]),
    )


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
        hold_seconds: int | None = None,
        now: datetime.datetime | None = None,
    ) -> SterilityIncubationRecord: ...

    def complete(
        self,
        tenant_id: str,
        batch_id: str,
        *,
        now: datetime.datetime | None = None,
    ) -> SterilityIncubationRecord: ...

    def bind_resume_handle(
        self,
        tenant_id: str,
        batch_id: str,
        handle: SterilityResumeHandle,
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

    def bind_resume_handle(
        self,
        tenant_id: str,
        batch_id: str,
        handle: SterilityResumeHandle,
    ) -> SterilityIncubationRecord:
        record = self.get(tenant_id, batch_id)
        if record is None:
            raise KeyError(f"No incubation for {tenant_id}/{batch_id}.")
        updated = record.model_copy(update={"resume_handle": handle})
        self._rows[(tenant_id, batch_id)] = updated
        return updated

    def tick_due(
        self, *, now: datetime.datetime | None = None
    ) -> list[SterilityIncubationRecord]:
        """Claim-then-return, so a row only appears once even if called twice."""
        stamp = now or _utcnow()
        done: list[SterilityIncubationRecord] = []
        for key, record in list(self._rows.items()):
            if (
                record.status is not SterilityIncubationStatus.PENDING
                or stamp < record.due_at
            ):
                continue
            updated = record.model_copy(
                update={
                    "status": SterilityIncubationStatus.COMPLETE,
                    "completed_at": stamp,
                }
            )
            self._rows[key] = updated
            done.append(updated)
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
                    app_name TEXT,
                    user_id TEXT,
                    session_id TEXT,
                    invocation_id TEXT,
                    interrupt_id TEXT,
                    PRIMARY KEY (tenant_id, batch_id)
                )
                """
            )
            existing = {
                row["name"]
                for row in db.execute(
                    "PRAGMA table_info(sterility_incubation)"
                ).fetchall()
            }
            for column in RESUME_HANDLE_COLUMNS:
                if column not in existing:
                    db.execute(
                        "ALTER TABLE sterility_incubation "
                        f"ADD COLUMN {column} TEXT"
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
            resume_handle=resume_handle_from_columns(
                {column: raw[column] for column in RESUME_HANDLE_COLUMNS}
            ),
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
        if self.get(tenant_id, batch_id) is None:
            raise KeyError(f"No incubation for {tenant_id}/{batch_id}.")
        with self._connect() as db:
            db.execute(
                """
                UPDATE sterility_incubation
                SET app_name = ?, user_id = ?, session_id = ?,
                    invocation_id = ?, interrupt_id = ?
                WHERE tenant_id = ? AND batch_id = ?
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
            db.commit()
        updated = self.get(tenant_id, batch_id)
        if updated is None:
            raise RuntimeError(
                f"Resume handle write failed for {tenant_id}/{batch_id}."
            )
        return updated

    def tick_due(
        self, *, now: datetime.datetime | None = None
    ) -> list[SterilityIncubationRecord]:
        """Atomic claim: the UPDATE itself decides which rows this call owns.

        A select-then-update pair would let two overlapping ticks both read
        the same pending row before either completed it, delivering the same
        wake-up twice. ``UPDATE ... WHERE status = 'pending' ... RETURNING``
        both claims and reads in one statement, so a row can only be
        returned by the tick that actually flipped it.
        """
        stamp = now or _utcnow()
        with self._connect() as db:
            rows = db.execute(
                """
                UPDATE sterility_incubation
                SET status = ?, completed_at = ?
                WHERE status = ? AND due_at <= ?
                RETURNING *
                """,
                (
                    SterilityIncubationStatus.COMPLETE.value,
                    stamp.isoformat(),
                    SterilityIncubationStatus.PENDING.value,
                    stamp.isoformat(),
                ),
            ).fetchall()
            db.commit()
        return [self._row(raw) for raw in rows]

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
