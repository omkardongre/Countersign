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

"""Named department-system tools. MCP wraps these functions.

Each read is delegated to ``app/domain/department_source.py``, which decides
whether the row comes from a real system (Cloud SQL for QC lab, BigQuery for
environmental monitoring) or the pinned synthetic catalog. The tool names,
signatures, and Gateway allow-list entries are unchanged by that choice: the
agent calling ``lims_query`` cannot tell, and must not be able to tell, which
backend answered it.

MES and QMS are not in this slice. Investigation must not call these tools.
"""

from __future__ import annotations

from app.domain.department_models import (
    CoaSnapshot,
    DriveEvidencePayload,
    EmSnapshot,
    LimsSnapshot,
)
from app.domain.department_source import (
    DepartmentRecordMissing,
    get_coa_source,
    get_em_source,
    get_lims_source,
)

__all__ = [
    "ALLOWED_DEPARTMENT_TOOLS",
    "CASE_MEMORY_TOOLS",
    "COA_FETCH",
    "DRIVE_FETCH_EVIDENCE",
    "DRIVE_TOOLS",
    "DepartmentRecordMissing",
    "DriveFetchError",
    "EM_QUERY",
    "LIMS_QUERY",
    "PRIOR_ART_QUERY",
    "PRIOR_ART_REDACT",
    "PRIOR_ART_WRITE",
    "STERILITY_BIND_RESUME_HANDLE",
    "STERILITY_GET",
    "STERILITY_START",
    "STERILITY_TOOLS",
    "coa_fetch",
    "drive_fetch_evidence",
    "em_query",
    "lims_query",
]

LIMS_QUERY = "lims_query"
EM_QUERY = "em_query"
COA_FETCH = "coa_fetch"
DRIVE_FETCH_EVIDENCE = "drive_fetch_evidence"
STERILITY_START = "sterility_start"
STERILITY_GET = "sterility_get"

# Read/write on a per-batch clock, not a department read. Kept out of
# ALLOWED_DEPARTMENT_TOOLS (the QC/supply collector allow-list) so it needs
# its own Gateway ALLOW entry rather than inheriting department scope.
STERILITY_BIND_RESUME_HANDLE = "sterility_bind_resume_handle"
STERILITY_TOOLS = frozenset(
    {STERILITY_START, STERILITY_GET, STERILITY_BIND_RESUME_HANDLE}
)

# Case Memory: prior-deviation facts, reached over MCP for the same reason
# as the sterility clock (an Agent Identity principal cannot itself hold
# Firestore IAM credentials). Query is read-only; write/redact are their own
# tools rather than piggybacking on a department name.
PRIOR_ART_QUERY = "prior_art_query"
PRIOR_ART_WRITE = "prior_art_write"
PRIOR_ART_REDACT = "prior_art_redact"
CASE_MEMORY_TOOLS = frozenset(
    {PRIOR_ART_QUERY, PRIOR_ART_WRITE, PRIOR_ART_REDACT}
)

ALLOWED_DEPARTMENT_TOOLS = frozenset({LIMS_QUERY, EM_QUERY, COA_FETCH})

# drive_fetch_evidence takes a file_id, unlike the fixed
# tenant_id/batch_id/lifecycle_state/case_tenant_id shape
# call_department_tool hardcodes for the other three reads, so it is called
# through call_drive_tool with its own allow-list instead.
DRIVE_TOOLS = frozenset({DRIVE_FETCH_EVIDENCE})


class DriveFetchError(Exception):
    """The Drive API call failed, or returned a file this ingest path rejects."""


def lims_query(*, tenant_id: str, batch_id: str) -> LimsSnapshot:
    """QC lab assay results for one tenant's batch. Read-only.

    Backed by a real Cloud SQL table when ``COUNTERSIGN_LIMS_BACKEND=cloud_sql``,
    otherwise by the pinned synthetic catalog. The returned ``system`` and
    ``source_uri`` name whichever one actually answered, so a citation always
    points at the system the value was read from.
    """
    return get_lims_source().lims(tenant_id=tenant_id, batch_id=batch_id)


def em_query(*, tenant_id: str, batch_id: str) -> EmSnapshot:
    """Environmental-monitoring readings for one tenant's batch. Read-only.

    Backed by a real BigQuery table when ``COUNTERSIGN_EM_BACKEND=bigquery``,
    otherwise by the pinned synthetic catalog.
    """
    return get_em_source().em(tenant_id=tenant_id, batch_id=batch_id)


def coa_fetch(*, tenant_id: str, batch_id: str) -> CoaSnapshot:
    """Supplier CoA in Drive-normalized connector shape.

    Still catalog-backed: the untrusted supplier channel is its own P1 item.
    """
    return get_coa_source().coa(tenant_id=tenant_id, batch_id=batch_id)


# MIME types the Drive API may return that ingest.py's allow-list also
# accepts (app/domain/evidence_models.py::ALLOWED_EVIDENCE_MIME_TYPES).
# Google-native formats (Docs/Sheets/Slides) have no fixed byte
# representation and are rejected rather than silently exported to PDF,
# since an exported copy is not the artifact the case would be citing.
_ALLOWED_DRIVE_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/webp",
    }
)

# Same per-artifact ceiling ingest.py enforces
# (app/domain/evidence_models.py::MAX_EVIDENCE_BYTES), checked here too so a
# huge Drive file is rejected before its bytes are base64-encoded and
# shipped over MCP, not just later at ingest.
_MAX_DRIVE_FETCH_BYTES = 20 * 1024 * 1024


def drive_fetch_evidence(*, tenant_id: str, file_id: str) -> DriveEvidencePayload:
    """Fetches one scanned BMR page from Google Drive as base64 bytes.

    Runs inside the MCP process, which holds the Drive credential — an
    Agent Identity principal cannot itself be a Drive OAuth/service-account
    principal any more than it can be a Cloud SQL or Firestore one (same
    reasoning as the sterility clock and Case Memory). The Drive folder
    containing scanned BMR pages is shared read-only with this process's
    service account; ``tenant_id`` is accepted for parity with the other
    department tools and for audit logging, not because Drive itself is
    tenant-partitioned in this slice.
    """
    import base64

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    from app.app_utils.services import get_google_credentials

    del tenant_id  # audit/logging parity only; Drive ACLs are per-file here
    try:
        credentials = get_google_credentials(
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        service = build("drive", "v3", credentials=credentials)
        meta = (
            service.files()
            .get(fileId=file_id, fields="id,name,mimeType,size,webViewLink")
            .execute()
        )
        mime_type = meta["mimeType"]
        if mime_type not in _ALLOWED_DRIVE_MIME_TYPES:
            raise DriveFetchError(
                f"Drive file {file_id} has MIME type {mime_type!r}, which "
                f"is not an accepted evidence type. Accepted: "
                f"{sorted(_ALLOWED_DRIVE_MIME_TYPES)}."
            )
        size = int(meta.get("size") or 0)
        if size > _MAX_DRIVE_FETCH_BYTES:
            raise DriveFetchError(
                f"Drive file {file_id} is {size} bytes, above the "
                f"{_MAX_DRIVE_FETCH_BYTES}-byte per-artifact limit."
            )
        payload = service.files().get_media(fileId=file_id).execute()
    except HttpError as exc:
        raise DriveFetchError(f"Drive API call for {file_id} failed: {exc}") from exc

    return DriveEvidencePayload(
        source_uri=meta.get("webViewLink") or f"drive://{file_id}",
        file_id=file_id,
        mime_type=mime_type,
        content_base64=base64.b64encode(payload).decode("ascii"),
    )


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


class CaseMemoryTenantMismatch(Exception):
    """The calling case is not the tenant that owns this fact/query."""


def _check_memory_tenant(tenant_id: str, case_tenant_id: str) -> None:
    if tenant_id != case_tenant_id:
        raise CaseMemoryTenantMismatch(
            f"case_tenant_id {case_tenant_id!r} does not match "
            f"tenant_id {tenant_id!r}."
        )


async def prior_art_query(
    *, tenant_id: str, query: str, case_tenant_id: str, top_k: int = 5
):
    """Tenant-filtered retrieval of prior-deviation facts. Advisory input only.

    Runs inside the MCP process, which holds real Firestore credentials —
    an Agent Identity principal cannot itself be a Firestore IAM principal
    any more than it can be a Cloud SQL one.
    """
    _check_memory_tenant(tenant_id, case_tenant_id)
    from app.domain.case_memory_models import CaseMemoryQueryResult
    from app.domain.case_memory_store import get_case_memory_store

    facts, embedded = await get_case_memory_store().query(
        tenant_id=tenant_id, query=query, top_k=top_k
    )
    return CaseMemoryQueryResult(
        tenant_id=tenant_id, query=query, facts=facts, query_embedded=embedded
    )


async def prior_art_write(
    *,
    tenant_id: str,
    case_id: str,
    batch_id: str,
    fact: str,
    source_uri: str,
    confidence: float,
    case_tenant_id: str,
    fact_id: str | None = None,
):
    """Write one fact about a case into Case Memory for future retrieval.

    ``fact_id`` is optional; a caller with a natural idempotency key (see
    ``app/domain/case_memory_node.py::record_case_memory``) should pass one
    so a resume-triggered rerun overwrites the same fact instead of writing
    a duplicate.
    """
    _check_memory_tenant(tenant_id, case_tenant_id)
    from app.domain.case_memory_store import get_case_memory_store

    return await get_case_memory_store().write_fact(
        tenant_id=tenant_id,
        case_id=case_id,
        batch_id=batch_id,
        fact=fact,
        source_uri=source_uri,
        confidence=confidence,
        fact_id=fact_id,
    )


async def prior_art_redact(
    *, tenant_id: str, fact_id: str, reason: str, case_tenant_id: str
):
    """Redact one poisoned or incorrect fact. Tenant-scoped, not a delete-all."""
    _check_memory_tenant(tenant_id, case_tenant_id)
    from app.domain.case_memory_store import get_case_memory_store

    return await get_case_memory_store().redact(
        tenant_id=tenant_id, fact_id=fact_id, reason=reason
    )
