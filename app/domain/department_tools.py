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
STERILITY_START = "sterility_start"
STERILITY_GET = "sterility_get"

# Read/write on a per-batch clock, not a department read. Kept out of
# ALLOWED_DEPARTMENT_TOOLS (the QC/supply collector allow-list) so it needs
# its own Gateway ALLOW entry rather than inheriting department scope.
STERILITY_BIND_RESUME_HANDLE = "sterility_bind_resume_handle"
STERILITY_TOOLS = frozenset(
    {STERILITY_START, STERILITY_GET, STERILITY_BIND_RESUME_HANDLE}
)

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


class SterilityTenantMismatch(Exception):
    """The calling case is not the tenant that owns this batch's clock."""


def _check_case_tenant(tenant_id: str, case_tenant_id: str) -> None:
    """Same cross-tenant check the department reads already enforce.

    The TQA callout only inspects ``lims_query``/``em_query``/``coa_fetch``
    (``infra/geap/tqa_spike/src/countersign_tqa/policy.py``), so the
    sterility tools must not rely on that extension: they check for
    themselves that the caller's own tenant matches the batch it is
    reading or arming a wake-up for.
    """
    if tenant_id != case_tenant_id:
        raise SterilityTenantMismatch(
            f"case_tenant_id {case_tenant_id!r} does not match "
            f"tenant_id {tenant_id!r}."
        )


def sterility_start(
    *,
    tenant_id: str,
    batch_id: str,
    case_id: str,
    workflow_version: str,
    case_tenant_id: str,
):
    """Start (or return the existing) incubation clock. Idempotent.

    Runs inside the MCP process, which holds a real Cloud SQL IAM
    credential. An Agent Identity principal cannot be a Cloud SQL IAM
    database user (name/type constraints), so the Runtime workflow reaches
    this clock through this tool rather than opening Cloud SQL itself.
    """
    _check_case_tenant(tenant_id, case_tenant_id)
    from app.domain.sterility_store import get_sterility_store

    return get_sterility_store().start(
        tenant_id=tenant_id,
        batch_id=batch_id,
        case_id=case_id,
        workflow_version=workflow_version,
    )


def sterility_get(*, tenant_id: str, batch_id: str, case_tenant_id: str):
    """Current incubation record, or ``None`` if none has started."""
    _check_case_tenant(tenant_id, case_tenant_id)
    from app.domain.sterility_store import get_sterility_store

    return get_sterility_store().get(tenant_id, batch_id)


def sterility_bind_resume_handle(
    *,
    tenant_id: str,
    batch_id: str,
    app_name: str,
    user_id: str,
    session_id: str,
    invocation_id: str,
    interrupt_id: str,
    case_tenant_id: str,
):
    """Record where the paused invocation lives, so a tick can wake it."""
    _check_case_tenant(tenant_id, case_tenant_id)
    from app.domain.sterility_models import SterilityResumeHandle
    from app.domain.sterility_store import get_sterility_store

    return get_sterility_store().bind_resume_handle(
        tenant_id,
        batch_id,
        SterilityResumeHandle(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            invocation_id=invocation_id,
            interrupt_id=interrupt_id,
        ),
    )
