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

"""Where department reads actually come from: real systems, or the catalog.

P1 in ``final_reseach/10_gap_analysis_and_win_plan.md``: QC lab moves onto a
real Cloud SQL table and environmental monitoring onto a real BigQuery table,
behind the department MCP tool boundary that already exists. The agents,
their tool allow-lists, and the deterministic checks do not change — only the
system the row is read from does.

Backend selection is an explicit opt-in per department, never inferred:

* ``COUNTERSIGN_LIMS_BACKEND=cloud_sql`` reads QC results from Cloud SQL.
* ``COUNTERSIGN_EM_BACKEND=bigquery`` reads EM readings from BigQuery.
* Anything else (including unset) keeps the pinned synthetic catalog.

Opt-in rather than "real if credentials happen to exist" is deliberate: a
half-provisioned deployment must keep working on the catalog instead of
silently failing every QC branch, and flipping a department to a real system
has to be a recorded deployment decision rather than a side effect of an
unrelated environment variable.

``backend_name()`` exists so ``app/domain/fleet_manifest.py`` can report the
system each agent is *actually* reading, rather than the system it is
eventually intended to read. A manifest that names a connector the code is
not using is an overclaim, and this is the seam that prevents it.
"""

from __future__ import annotations

import functools
import os
import re
from typing import Protocol

from app.domain.department_catalog import lookup_department_snapshot
from app.domain.department_models import CoaSnapshot, EmSnapshot, LimsSnapshot

LIMS_BACKEND_ENV = "COUNTERSIGN_LIMS_BACKEND"
EM_BACKEND_ENV = "COUNTERSIGN_EM_BACKEND"

LIMS_DB_ENV = "COUNTERSIGN_LIMS_DB"
LIMS_TABLE_ENV = "COUNTERSIGN_LIMS_TABLE"
EM_DATASET_ENV = "COUNTERSIGN_EM_DATASET"
EM_TABLE_ENV = "COUNTERSIGN_EM_TABLE"

DEFAULT_LIMS_DB = "countersign_lims"
DEFAULT_LIMS_TABLE = "lims_results"
DEFAULT_EM_DATASET = "countersign_em"
DEFAULT_EM_TABLE = "em_readings"

BACKEND_CLOUD_SQL = "cloud_sql"
BACKEND_BIGQUERY = "bigquery"
BACKEND_CATALOG = "synthetic_catalog"

# SQL identifiers cannot be bound as query parameters, so any table/dataset
# name that reaches a query string is checked against this instead of being
# trusted. Values come from deployment env, which is not a user input path,
# but an unvalidated identifier splice is an injection sink regardless of who
# is expected to set it.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class DepartmentRecordMissing(Exception):
    """No row in the backing system for the requested tenant/batch.

    Raised identically by every backend so a branch fails closed the same
    way whether the miss came from Cloud SQL, BigQuery, or the catalog.
    """


def require_identifier(value: str, *, label: str) -> str:
    if not _IDENTIFIER.match(value):
        raise RuntimeError(
            f"{label} {value!r} is not a valid unquoted SQL identifier."
        )
    return value


def lims_db_name() -> str:
    return require_identifier(
        os.environ.get(LIMS_DB_ENV, "").strip() or DEFAULT_LIMS_DB,
        label=LIMS_DB_ENV,
    )


def lims_table_name() -> str:
    return require_identifier(
        os.environ.get(LIMS_TABLE_ENV, "").strip() or DEFAULT_LIMS_TABLE,
        label=LIMS_TABLE_ENV,
    )


def em_dataset_name() -> str:
    return require_identifier(
        os.environ.get(EM_DATASET_ENV, "").strip() or DEFAULT_EM_DATASET,
        label=EM_DATASET_ENV,
    )


def em_table_name() -> str:
    return require_identifier(
        os.environ.get(EM_TABLE_ENV, "").strip() or DEFAULT_EM_TABLE,
        label=EM_TABLE_ENV,
    )


class LimsSource(Protocol):
    def lims(self, *, tenant_id: str, batch_id: str) -> LimsSnapshot: ...


class EmSource(Protocol):
    def em(self, *, tenant_id: str, batch_id: str) -> EmSnapshot: ...


class CatalogDepartmentSource:
    """The pinned synthetic catalog. Still the source for CoA, and the
    fallback for QC/EM when their real backend is not switched on.
    """

    backend_name = BACKEND_CATALOG

    def _snapshot(self, *, tenant_id: str, batch_id: str):
        snapshot = lookup_department_snapshot(
            tenant_id=tenant_id, batch_id=batch_id
        )
        if snapshot is None:
            raise DepartmentRecordMissing(
                f"No department records for {tenant_id}/{batch_id}."
            )
        return snapshot

    def lims(self, *, tenant_id: str, batch_id: str) -> LimsSnapshot:
        return self._snapshot(tenant_id=tenant_id, batch_id=batch_id).lims

    def em(self, *, tenant_id: str, batch_id: str) -> EmSnapshot:
        return self._snapshot(tenant_id=tenant_id, batch_id=batch_id).em

    def coa(self, *, tenant_id: str, batch_id: str) -> CoaSnapshot:
        return self._snapshot(tenant_id=tenant_id, batch_id=batch_id).coa


@functools.cache
def get_lims_source() -> LimsSource:
    """QC lab reads. Cloud SQL only when explicitly switched on."""
    requested = os.environ.get(LIMS_BACKEND_ENV, "").strip().lower()
    if requested == BACKEND_CLOUD_SQL:
        from app.app_utils.cloud_sql import instance_connection_name

        if not instance_connection_name():
            raise RuntimeError(
                f"{LIMS_BACKEND_ENV}={BACKEND_CLOUD_SQL} requires "
                "INSTANCE_CONNECTION_NAME to be set."
            )
        from app.domain.department_source_postgres import PostgresLimsSource

        return PostgresLimsSource()
    return CatalogDepartmentSource()


@functools.cache
def get_em_source() -> EmSource:
    """Environmental monitoring reads. BigQuery only when switched on."""
    requested = os.environ.get(EM_BACKEND_ENV, "").strip().lower()
    if requested == BACKEND_BIGQUERY:
        from app.domain.department_source_bigquery import BigQueryEmSource

        return BigQueryEmSource()
    return CatalogDepartmentSource()


@functools.cache
def get_coa_source() -> CatalogDepartmentSource:
    """Supplier CoA. Still catalog-backed; the untrusted supplier channel is
    its own P1 item, not part of the QC/EM move.
    """
    return CatalogDepartmentSource()


def lims_backend_name() -> str:
    return getattr(get_lims_source(), "backend_name", BACKEND_CATALOG)


def em_backend_name() -> str:
    return getattr(get_em_source(), "backend_name", BACKEND_CATALOG)


def coa_backend_name() -> str:
    return BACKEND_CATALOG


def reset_department_source_cache() -> None:
    get_lims_source.cache_clear()
    get_em_source.cache_clear()
    get_coa_source.cache_clear()
