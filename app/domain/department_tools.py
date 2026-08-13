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

"""Named department-system tools. Catalog-backed; MCP wraps these functions.

MES and QMS are not in this slice. Investigation must not call these tools.
"""

from __future__ import annotations

from app.domain.department_catalog import lookup_department_snapshot
from app.domain.department_models import CoaSnapshot, EmSnapshot, LimsSnapshot

LIMS_QUERY = "lims_query"
EM_QUERY = "em_query"
COA_FETCH = "coa_fetch"

ALLOWED_DEPARTMENT_TOOLS = frozenset({LIMS_QUERY, EM_QUERY, COA_FETCH})


class DepartmentRecordMissing(Exception):
    """No catalog row for the requested tenant/batch."""


def _snapshot(*, tenant_id: str, batch_id: str):
    snapshot = lookup_department_snapshot(tenant_id=tenant_id, batch_id=batch_id)
    if snapshot is None:
        raise DepartmentRecordMissing(
            f"No department records for {tenant_id}/{batch_id}."
        )
    return snapshot


def lims_query(*, tenant_id: str, batch_id: str) -> LimsSnapshot:
    """LIMS assay rows in Cloud SQL connector shape."""
    return _snapshot(tenant_id=tenant_id, batch_id=batch_id).lims


def em_query(*, tenant_id: str, batch_id: str) -> EmSnapshot:
    """Environmental-monitoring rows in BigQuery connector shape."""
    return _snapshot(tenant_id=tenant_id, batch_id=batch_id).em


def coa_fetch(*, tenant_id: str, batch_id: str) -> CoaSnapshot:
    """Supplier CoA in Drive-normalized connector shape."""
    return _snapshot(tenant_id=tenant_id, batch_id=batch_id).coa
