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

"""Real QC lab system: assay results in Cloud SQL Postgres.

Runs inside the department MCP process, which holds the Cloud SQL IAM
credential, for the same reason the sterility clock does: an Agent Identity
principal cannot itself be a Cloud SQL IAM database user, so the QC lab agent
reaches this table through its ``lims_query`` tool rather than opening a
database connection of its own. The agent therefore never holds a LIMS
credential — the tool boundary is the credential boundary.

Read-only by construction: this class issues ``SELECT`` only. No agent path
can write, update, or delete a laboratory result, which is a GMP data
integrity requirement (ALCOA+), not merely a design preference.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from app.app_utils.cloud_sql import connect_pg8000, instance_connection_name
from app.domain.department_models import LimsResultRow, LimsSnapshot
from app.domain.department_source import (
    BACKEND_CLOUD_SQL,
    DepartmentRecordMissing,
    lims_db_name,
    lims_table_name,
    sibling_batch_table_name,
)

LIMS_DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {table} (
    tenant_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    test_code TEXT NOT NULL,
    label TEXT NOT NULL,
    result_value DOUBLE PRECISION NOT NULL,
    unit TEXT NOT NULL,
    spec_min DOUBLE PRECISION NOT NULL,
    spec_max DOUBLE PRECISION NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, batch_id, sample_id, test_code)
)
"""

# tenant_id leads the index because every read is tenant-scoped; a query that
# forgot the tenant predicate would not be able to use it.
LIMS_INDEX_TEMPLATE = """
CREATE INDEX IF NOT EXISTS {table}_tenant_batch_idx
    ON {table} (tenant_id, batch_id)
"""

_SELECT_TEMPLATE = """
SELECT test_code, label, result_value, unit, spec_min, spec_max, sample_id
FROM {table}
WHERE tenant_id = %s AND batch_id = %s
ORDER BY test_code, sample_id
"""


@contextmanager
def _connect() -> Iterator:
    conn = connect_pg8000(database=lims_db_name())
    try:
        yield conn
    finally:
        conn.close()


class PostgresLimsSource:
    """Cloud SQL-backed QC lab reads."""

    backend_name = BACKEND_CLOUD_SQL

    def source_uri(self, *, tenant_id: str, batch_id: str) -> str:
        """A citable identifier of the exact query this row came from.

        Deterministic gates admit a claim only if its ``source_uri`` is in the
        case's allow-list, so this has to name the real instance, database,
        and table rather than a generic label — a citation that cannot be
        traced back to a system of record is not an audit trail.
        """
        return (
            f"cloudsql://{instance_connection_name()}/{lims_db_name()}"
            f"/{lims_table_name()}?tenant_id={tenant_id}&batch_id={batch_id}"
        )

    def lims(self, *, tenant_id: str, batch_id: str) -> LimsSnapshot:
        table = lims_table_name()
        with _connect() as conn:
            cursor = conn.cursor()
            # Parameterized: tenant_id and batch_id are bound values, never
            # spliced. The table name is validated as an identifier by
            # lims_table_name() because SQL cannot bind identifiers.
            cursor.execute(
                _SELECT_TEMPLATE.format(table=table), (tenant_id, batch_id)
            )
            rows = cursor.fetchall()
        if not rows:
            raise DepartmentRecordMissing(
                f"No LIMS results in {table} for {tenant_id}/{batch_id}."
            )
        return LimsSnapshot(
            system=BACKEND_CLOUD_SQL,
            source_uri=self.source_uri(tenant_id=tenant_id, batch_id=batch_id),
            rows=[
                LimsResultRow(
                    test_code=raw[0],
                    label=raw[1],
                    result_value=float(raw[2]),
                    unit=raw[3],
                    spec_min=float(raw[4]),
                    spec_max=float(raw[5]),
                    sample_id=raw[6],
                )
                for raw in rows
            ],
        )


SIBLING_BATCH_DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {table} (
    tenant_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    material_lot TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, batch_id)
)
"""

# tenant_id + material_lot leads the index because every sibling-batch scan
# is "this tenant, this lot" — the same tenant-first discipline as the LIMS
# index, so a query that omitted the tenant predicate could not use it.
SIBLING_BATCH_INDEX_TEMPLATE = """
CREATE INDEX IF NOT EXISTS {table}_tenant_lot_idx
    ON {table} (tenant_id, material_lot)
"""

_SIBLING_SOURCE_LOT_TEMPLATE = """
SELECT material_lot FROM {table} WHERE tenant_id = %s AND batch_id = %s
"""

_SIBLING_MATCHES_TEMPLATE = """
SELECT batch_id, material_lot, source_uri FROM {table}
WHERE tenant_id = %s AND material_lot = %s AND batch_id != %s
ORDER BY batch_id
"""


class PostgresSiblingBatchSource:
    """Cloud SQL-backed cross-batch material-lot reads for CFI scope
    expansion (21 CFR 211.192's extend-to-associated-batches duty).

    A dedicated table, not a join against ``lims_results`` or the supplier
    CoA path: material lot is recorded once per batch regardless of which
    LIMS assay rows exist, and the untrusted supplier-CoA channel (P1, not
    yet moved off the catalog) must not become this table's source of
    truth. Read-only by construction: ``SELECT`` only, matching every other
    Cloud SQL-backed department read in this module.
    """

    backend_name_sibling_batch = BACKEND_CLOUD_SQL

    def source_uri(self, *, tenant_id: str, batch_id: str) -> str:
        table = sibling_batch_table_name()
        return (
            f"cloudsql://{instance_connection_name()}/{lims_db_name()}"
            f"/{table}?tenant_id={tenant_id}&batch_id={batch_id}"
        )

    def batches_sharing_material_lot(
        self, *, tenant_id: str, batch_id: str
    ) -> tuple[str, list[tuple[str, str, str]]]:
        table = sibling_batch_table_name()
        with _connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                _SIBLING_SOURCE_LOT_TEMPLATE.format(table=table),
                (tenant_id, batch_id),
            )
            source_row = cursor.fetchone()
            if source_row is None:
                raise DepartmentRecordMissing(
                    f"No material-lot record in {table} for "
                    f"{tenant_id}/{batch_id}."
                )
            source_lot = source_row[0]
            cursor.execute(
                _SIBLING_MATCHES_TEMPLATE.format(table=table),
                (tenant_id, source_lot, batch_id),
            )
            matches = cursor.fetchall()
        return source_lot, [
            (raw[0], raw[1], raw[2]) for raw in matches
        ]
