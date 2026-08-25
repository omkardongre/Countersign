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

"""Cloud SQL ``cfi_interview`` aggregate store.

Same shape and reasoning as ``cfi_store_postgres.py``: the department MCP
process owns this connection; the CFI Runtime never opens Cloud SQL
directly. A unique ``(tenant_id, cfi_id, hypothesis_id, round_number)`` key makes
interview start an atomic insert-or-read; a monotonically increasing
``revision`` makes every update compare-and-swap.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import pg8000.exceptions

from app.app_utils.cloud_sql import connect_pg8000
from app.domain.cfi_interview_models import CfiInterviewRecord, CfiInterviewStatus
from app.domain.cfi_interview_store import (
    CfiInterviewConcurrencyError,
    CfiInterviewNotFoundError,
)

CFI_INTERVIEW_DDL = """
CREATE TABLE IF NOT EXISTS cfi_interview (
    interview_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    cfi_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    round_number INTEGER NOT NULL CHECK (round_number >= 1),
    status TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cfi_interview_tenant_cfi_hyp_round_key
        UNIQUE (tenant_id, cfi_id, hypothesis_id, round_number)
)
"""

CFI_INTERVIEW_INDEX = """
CREATE INDEX IF NOT EXISTS cfi_interview_status_idx
    ON cfi_interview (status)
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
        f"cfi_interview.payload returned unsupported type {type(raw).__name__}."
    )


class PostgresCfiInterviewStore:
    def __init__(self) -> None:
        with _connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(CFI_INTERVIEW_DDL)
                cursor.execute(CFI_INTERVIEW_INDEX)
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
        cursor.execute("SELECT to_regclass('public.cfi_interview') IS NOT NULL")
        (exists,) = cursor.fetchone()
        if not exists:
            raise RuntimeError(
                "cfi_interview table does not exist and this connection "
                "lacks privilege to create it."
            )

    def open(self, record: CfiInterviewRecord) -> CfiInterviewRecord:
        if record.revision != 0:
            raise CfiInterviewConcurrencyError(
                "A new interview must be opened with revision 0."
            )
        payload = record.model_dump(mode="json")
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO cfi_interview (
                    interview_id, tenant_id, cfi_id, hypothesis_id, round_number,
                    status, revision, payload, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT DO NOTHING
                RETURNING payload
                """,
                (
                    record.interview_id,
                    record.tenant_id,
                    record.cfi_id,
                    record.hypothesis_id,
                    record.round_number,
                    record.status.value,
                    0,
                    json.dumps(payload),
                ),
            )
            raw = cursor.fetchone()
            if raw is None:
                cursor.execute(
                    """
                    SELECT payload FROM cfi_interview
                    WHERE tenant_id = %s AND cfi_id = %s
                      AND hypothesis_id = %s AND round_number = %s
                    """,
                    (
                        record.tenant_id,
                        record.cfi_id,
                        record.hypothesis_id,
                        record.round_number,
                    ),
                )
                raw = cursor.fetchone()
        if raw is None:
            raise CfiInterviewConcurrencyError(
                "Interview id conflicts with an existing row that was not readable."
            )
        return CfiInterviewRecord.model_validate(_decode_payload(raw[0]))

    def get(self, interview_id: str) -> CfiInterviewRecord | None:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT payload FROM cfi_interview WHERE interview_id = %s",
                (interview_id,),
            )
            raw = cursor.fetchone()
        if raw is None:
            return None
        return CfiInterviewRecord.model_validate(_decode_payload(raw[0]))

    def save(self, record: CfiInterviewRecord) -> CfiInterviewRecord:
        payload = record.model_dump(mode="json")
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT tenant_id, cfi_id, hypothesis_id, round_number, revision
                FROM cfi_interview
                WHERE interview_id = %s
                FOR UPDATE
                """,
                (record.interview_id,),
            )
            current = cursor.fetchone()
            if current is None:
                raise CfiInterviewNotFoundError(record.interview_id)
            current_tenant, current_cfi, current_hyp, current_round, current_revision = current
            if (
                current_tenant != record.tenant_id
                or current_cfi != record.cfi_id
                or current_hyp != record.hypothesis_id
                or current_round != record.round_number
            ):
                raise CfiInterviewConcurrencyError(
                    "Interview tenant, CFI, hypothesis, and round identity are immutable."
                )
            if current_revision != record.revision:
                raise CfiInterviewConcurrencyError(
                    f"Stale interview revision {record.revision}; current is "
                    f"{current_revision}."
                )
            cursor.execute(
                """
                UPDATE cfi_interview
                SET status = %s,
                    revision = revision + 1,
                    payload = jsonb_set(
                        %s::jsonb,
                        '{revision}',
                        to_jsonb(revision + 1),
                        true
                    ),
                    updated_at = now()
                WHERE interview_id = %s AND revision = %s
                RETURNING payload
                """,
                (
                    record.status.value,
                    json.dumps(payload),
                    record.interview_id,
                    record.revision,
                ),
            )
            updated = cursor.fetchone()
        if updated is None:
            raise CfiInterviewConcurrencyError(
                f"Interview revision changed while saving {record.interview_id}."
            )
        return CfiInterviewRecord.model_validate(_decode_payload(updated[0]))

    def list_waiting(self) -> list[CfiInterviewRecord]:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT payload FROM cfi_interview
                WHERE status IN (%s, %s)
                ORDER BY updated_at, interview_id
                """,
                (
                    CfiInterviewStatus.WAITING_FOR_REPLY.value,
                    CfiInterviewStatus.POLLING.value,
                ),
            )
            rows = cursor.fetchall()
        return [
            CfiInterviewRecord.model_validate(_decode_payload(raw[0]))
            for raw in rows
        ]

    def claim_waiting(
        self, *, now, lease_seconds: int, limit: int = 100
    ) -> list[CfiInterviewRecord]:
        from datetime import timedelta

        lease_until = now + timedelta(seconds=lease_seconds)
        claimed: list[CfiInterviewRecord] = []
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT interview_id, payload
                FROM cfi_interview
                WHERE status IN (%s, %s, %s)
                   OR (status = %s AND (payload->>'poll_lease_until') IS NOT NULL
                       AND (payload->>'poll_lease_until')::timestamptz <= %s)
                ORDER BY updated_at, interview_id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (
                    CfiInterviewStatus.WAITING_FOR_REPLY.value,
                    CfiInterviewStatus.REPLY_CAPTURED.value,
                    CfiInterviewStatus.INTERVIEW_UNANSWERED.value,
                    CfiInterviewStatus.POLLING.value,
                    now,
                    limit,
                ),
            )
            rows = cursor.fetchall()
            for interview_id, raw_payload in rows:
                record = CfiInterviewRecord.model_validate(
                    _decode_payload(raw_payload)
                )
                updated = record.model_copy(
                    update={
                        "status": CfiInterviewStatus.POLLING,
                        "poll_lease_until": lease_until,
                    }
                )
                payload = updated.model_dump(mode="json")
                cursor.execute(
                    """
                    UPDATE cfi_interview
                    SET status = %s,
                        revision = revision + 1,
                        payload = jsonb_set(
                            %s::jsonb,
                            '{revision}',
                            to_jsonb(revision + 1),
                            true
                        ),
                        updated_at = now()
                    WHERE interview_id = %s
                    RETURNING payload
                    """,
                    (
                        CfiInterviewStatus.POLLING.value,
                        json.dumps(payload),
                        interview_id,
                    ),
                )
                saved = cursor.fetchone()
                if saved is not None:
                    claimed.append(
                        CfiInterviewRecord.model_validate(_decode_payload(saved[0]))
                    )
        return claimed

    def release_poll(self, interview_id: str) -> CfiInterviewRecord | None:
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT payload FROM cfi_interview
                WHERE interview_id = %s
                FOR UPDATE
                """,
                (interview_id,),
            )
            raw = cursor.fetchone()
            if raw is None:
                return None
            record = CfiInterviewRecord.model_validate(_decode_payload(raw[0]))
            if record.status is not CfiInterviewStatus.POLLING:
                return record
            updated = record.model_copy(
                update={
                    "status": CfiInterviewStatus.WAITING_FOR_REPLY,
                    "poll_lease_until": None,
                }
            )
            cursor.execute(
                """
                UPDATE cfi_interview
                SET status = %s,
                    revision = revision + 1,
                    payload = jsonb_set(
                        %s::jsonb,
                        '{revision}',
                        to_jsonb(revision + 1),
                        true
                    ),
                    updated_at = now()
                WHERE interview_id = %s
                RETURNING payload
                """,
                (
                    CfiInterviewStatus.WAITING_FOR_REPLY.value,
                    json.dumps(updated.model_dump(mode="json")),
                    interview_id,
                ),
            )
            saved = cursor.fetchone()
        return (
            None
            if saved is None
            else CfiInterviewRecord.model_validate(_decode_payload(saved[0]))
        )

    def reset(self) -> None:
        with _connect() as conn:
            conn.cursor().execute("DELETE FROM cfi_interview")
