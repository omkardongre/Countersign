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

"""Cloud SQL ``cfi_investigation`` aggregate store.

The department MCP process owns this connection. The CFI Runtime never opens
Cloud SQL directly; it calls ``cfi_open``, ``cfi_get``, and ``cfi_save`` over
MCP because Agent Identity is not a Cloud SQL database user.

The table has two concurrency protections:

* a unique ``(tenant_id, source_case_id)`` key makes the case-graph handoff
  an atomic insert-or-read operation;
* a monotonically increasing ``revision`` makes every aggregate update
  compare-and-swap. A stale Runtime invocation gets a typed concurrency
  error instead of overwriting a newer hypothesis/evidence ledger.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Iterator
from contextlib import contextmanager

import pg8000.exceptions

from app.app_utils.cloud_sql import connect_pg8000
from app.domain.cfi_launch_models import CfiLaunchItem
from app.domain.cfi_models import CfiInvestigation
from app.domain.cfi_store import CfiConcurrencyError, CfiNotFoundError

CFI_INVESTIGATION_DDL = """
CREATE TABLE IF NOT EXISTS cfi_investigation (
    cfi_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    source_case_id TEXT NOT NULL,
    outcome TEXT,
    revision BIGINT NOT NULL DEFAULT 0,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cfi_investigation_tenant_case_key
        UNIQUE (tenant_id, source_case_id)
)
"""

CFI_INVESTIGATION_MIGRATIONS = (
    "ALTER TABLE cfi_investigation ADD COLUMN IF NOT EXISTS "
    "revision BIGINT NOT NULL DEFAULT 0",
    "CREATE UNIQUE INDEX IF NOT EXISTS "
    "cfi_investigation_tenant_case_uidx ON cfi_investigation "
    "(tenant_id, source_case_id)",
)

CFI_INVESTIGATION_INDEX = """
CREATE INDEX IF NOT EXISTS cfi_investigation_tenant_batch_idx
    ON cfi_investigation (tenant_id, batch_id)
"""

_INSUFFICIENT_PRIVILEGE = "42501"


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


def _decode_payload(raw: object) -> dict:
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    raise RuntimeError(
        f"cfi_investigation.payload returned unsupported type "
        f"{type(raw).__name__}."
    )


class PostgresCfiStore:
    def __init__(self) -> None:
        with _connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(CFI_INVESTIGATION_DDL)
                for statement in CFI_INVESTIGATION_MIGRATIONS:
                    cursor.execute(statement)
                cursor.execute(CFI_INVESTIGATION_INDEX)
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
        cursor.execute(
            "SELECT to_regclass('public.cfi_investigation') IS NOT NULL"
        )
        (exists,) = cursor.fetchone()
        if not exists:
            raise RuntimeError(
                "cfi_investigation table does not exist and this "
                "connection lacks privilege to create it."
            )

    def open(self, investigation: CfiInvestigation) -> CfiInvestigation:
        if investigation.revision != 0:
            raise CfiConcurrencyError(
                "A new CFI must be opened with revision 0."
            )
        payload = investigation.model_dump(mode="json")
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO cfi_investigation (
                    cfi_id, tenant_id, batch_id, source_case_id, outcome,
                    revision, payload, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT DO NOTHING
                RETURNING payload
                """,
                (
                    investigation.cfi_id,
                    investigation.tenant_id,
                    investigation.batch_id,
                    investigation.source_case_id,
                    None,
                    0,
                    json.dumps(payload),
                ),
            )
            raw = cursor.fetchone()
            if raw is None:
                cursor.execute(
                    """
                    SELECT payload FROM cfi_investigation
                    WHERE tenant_id = %s AND source_case_id = %s
                    """,
                    (investigation.tenant_id, investigation.source_case_id),
                )
                raw = cursor.fetchone()
        if raw is None:
            raise CfiConcurrencyError(
                "CFI id conflicts with an existing different tenant/source "
                "case, or the conflicting row was not readable."
            )
        stored = CfiInvestigation.model_validate(_decode_payload(raw[0]))
        if stored.batch_id != investigation.batch_id:
            raise CfiConcurrencyError(
                "The tenant/source case is already bound to a different batch."
            )
        return stored

    def get(self, cfi_id: str) -> CfiInvestigation | None:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT payload FROM cfi_investigation WHERE cfi_id = %s",
                (cfi_id,),
            )
            raw = cursor.fetchone()
        if raw is None:
            return None
        return CfiInvestigation.model_validate(_decode_payload(raw[0]))

    def save(self, investigation: CfiInvestigation) -> CfiInvestigation:
        payload = investigation.model_dump(mode="json")
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT tenant_id, source_case_id, batch_id, revision
                FROM cfi_investigation
                WHERE cfi_id = %s
                FOR UPDATE
                """,
                (investigation.cfi_id,),
            )
            current = cursor.fetchone()
            if current is None:
                raise CfiNotFoundError(investigation.cfi_id)
            current_tenant, current_case, current_batch, current_revision = current
            if (
                current_tenant != investigation.tenant_id
                or current_case != investigation.source_case_id
                or current_batch != investigation.batch_id
            ):
                raise CfiConcurrencyError(
                    "CFI tenant, source-case, and batch identity are immutable."
                )
            if current_revision != investigation.revision:
                raise CfiConcurrencyError(
                    f"Stale CFI revision {investigation.revision}; current is "
                    f"{current_revision}."
                )
            cursor.execute(
                """
                UPDATE cfi_investigation
                SET outcome = %s,
                    revision = revision + 1,
                    payload = jsonb_set(
                        %s::jsonb,
                        '{revision}',
                        to_jsonb(revision + 1),
                        true
                    ),
                    updated_at = now()
                WHERE cfi_id = %s AND revision = %s
                RETURNING payload
                """,
                (
                    investigation.outcome.value if investigation.outcome else None,
                    json.dumps(payload),
                    investigation.cfi_id,
                    investigation.revision,
                ),
            )
            updated = cursor.fetchone()
        if updated is None:
            raise CfiConcurrencyError(
                f"CFI revision changed while saving {investigation.cfi_id}."
            )
        return CfiInvestigation.model_validate(_decode_payload(updated[0]))

    def list_open_for_batch(
        self, *, tenant_id: str, batch_id: str
    ) -> list[CfiInvestigation]:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT payload FROM cfi_investigation
                WHERE tenant_id = %s AND batch_id = %s AND outcome IS NULL
                ORDER BY updated_at, cfi_id
                """,
                (tenant_id, batch_id),
            )
            rows = cursor.fetchall()
        return [
            CfiInvestigation.model_validate(_decode_payload(raw[0]))
            for raw in rows
        ]

    def count_for_tenant(self, *, tenant_id: str, exclude_cfi_id: str) -> int:
        """Count this tenant's other CFI rows for recurrence evidence."""
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM cfi_investigation
                WHERE tenant_id = %s AND cfi_id <> %s
                """,
                (tenant_id, exclude_cfi_id),
            )
            (count,) = cursor.fetchone()
        return int(count)

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

        lease_until = now + timedelta(seconds=lease_seconds)
        claimed: list[CfiLaunchItem] = []
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT cfi_id, payload
                FROM cfi_investigation
                WHERE outcome IS NULL
                  AND (
                    COALESCE(payload->>'launch_status', 'started') IN ('pending', 'failed')
                    OR (
                      COALESCE(payload->>'launch_status', 'started') = 'launching'
                      AND (payload->>'launch_lease_until') IS NOT NULL
                      AND (payload->>'launch_lease_until')::timestamptz <= %s
                    )
                  )
                  AND COALESCE((payload->>'launch_attempts')::integer, 0) < %s
                ORDER BY updated_at, cfi_id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (now, max_attempts, limit),
            )
            rows = cursor.fetchall()
            for cfi_id, raw_payload in rows:
                record = CfiInvestigation.model_validate(_decode_payload(raw_payload))
                if record.launch_status not in {
                    CfiLaunchStatus.PENDING,
                    CfiLaunchStatus.FAILED,
                    CfiLaunchStatus.LAUNCHING,
                }:
                    continue
                updated = record.model_copy(
                    update={
                        "launch_status": CfiLaunchStatus.LAUNCHING,
                        "launch_attempts": record.launch_attempts + 1,
                        "launch_lease_until": lease_until,
                        "launch_error": None,
                    }
                )
                cursor.execute(
                    """
                    UPDATE cfi_investigation
                    SET revision = revision + 1,
                        payload = jsonb_set(
                            %s::jsonb,
                            '{revision}',
                            to_jsonb(revision + 1),
                            true
                        ),
                        updated_at = now()
                    WHERE cfi_id = %s
                    RETURNING payload
                    """,
                    (json.dumps(updated.model_dump(mode="json")), cfi_id),
                )
                saved = cursor.fetchone()
                if saved is None:
                    continue
                saved_record = CfiInvestigation.model_validate(
                    _decode_payload(saved[0])
                )
                claimed.append(
                    CfiLaunchItem(
                        cfi_id=saved_record.cfi_id,
                        tenant_id=saved_record.tenant_id,
                        source_case_id=saved_record.source_case_id,
                        batch_id=saved_record.batch_id,
                        launch_attempts=saved_record.launch_attempts,
                        launch_status=saved_record.launch_status,
                    )
                )
        return claimed

    def mark_launch_started(
        self, *, tenant_id: str, cfi_id: str, invocation_id: str
    ) -> CfiLaunchItem:
        from app.domain.cfi_models import CfiLaunchStatus

        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT payload FROM cfi_investigation WHERE cfi_id = %s FOR UPDATE",
                (cfi_id,),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise CfiNotFoundError(cfi_id)
            record = CfiInvestigation.model_validate(_decode_payload(raw[0]))
            if record.tenant_id != tenant_id:
                raise CfiConcurrencyError("CFI launch tenant does not match.")
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
                update={
                    "launch_status": CfiLaunchStatus.STARTED,
                    "launch_lease_until": None,
                    "launch_error": None,
                    "runtime_invocation_id": invocation_id,
                }
            )
            cursor.execute(
                """
                UPDATE cfi_investigation
                SET revision = revision + 1,
                    payload = jsonb_set(
                        %s::jsonb,
                        '{revision}',
                        to_jsonb(revision + 1),
                        true
                    ),
                    updated_at = now()
                WHERE cfi_id = %s
                RETURNING payload
                """,
                (json.dumps(updated.model_dump(mode="json")), cfi_id),
            )
            saved = cursor.fetchone()
        if saved is None:
            raise CfiConcurrencyError(f"CFI launch update failed for {cfi_id!r}.")
        saved_record = CfiInvestigation.model_validate(_decode_payload(saved[0]))
        return CfiLaunchItem(
            cfi_id=saved_record.cfi_id,
            tenant_id=saved_record.tenant_id,
            source_case_id=saved_record.source_case_id,
            batch_id=saved_record.batch_id,
            launch_attempts=saved_record.launch_attempts,
            launch_status=saved_record.launch_status,
        )

    def mark_launch_failed(
        self, *, tenant_id: str, cfi_id: str, error: str
    ) -> CfiLaunchItem:
        from app.domain.cfi_models import CfiLaunchStatus

        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT payload FROM cfi_investigation WHERE cfi_id = %s FOR UPDATE",
                (cfi_id,),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise CfiNotFoundError(cfi_id)
            record = CfiInvestigation.model_validate(_decode_payload(raw[0]))
            if record.tenant_id != tenant_id:
                raise CfiConcurrencyError("CFI launch tenant does not match.")
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
                update={
                    "launch_status": CfiLaunchStatus.FAILED,
                    "launch_lease_until": None,
                    "launch_error": error[:2000],
                }
            )
            cursor.execute(
                """
                UPDATE cfi_investigation
                SET revision = revision + 1,
                    payload = jsonb_set(
                        %s::jsonb,
                        '{revision}',
                        to_jsonb(revision + 1),
                        true
                    ),
                    updated_at = now()
                WHERE cfi_id = %s
                RETURNING payload
                """,
                (json.dumps(updated.model_dump(mode="json")), cfi_id),
            )
            saved = cursor.fetchone()
        if saved is None:
            raise CfiConcurrencyError(f"CFI launch update failed for {cfi_id!r}.")
        saved_record = CfiInvestigation.model_validate(_decode_payload(saved[0]))
        return CfiLaunchItem(
            cfi_id=saved_record.cfi_id,
            tenant_id=saved_record.tenant_id,
            source_case_id=saved_record.source_case_id,
            batch_id=saved_record.batch_id,
            launch_attempts=saved_record.launch_attempts,
            launch_status=saved_record.launch_status,
        )

    def reset(self) -> None:
        with _connect() as conn:
            conn.cursor().execute("DELETE FROM cfi_investigation")
