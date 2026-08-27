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

import datetime
import hashlib
import os

from app.domain.calendar_tools import (
    CfiCalendarError,
)
from app.domain.calendar_tools import (
    schedule_site_review as persist_site_review,
)
from app.domain.cfi_artifact_models import (
    CfiReportWriteRequest,
    CfiReportWriteResult,
    CfiSiteReviewReference,
    CfiSiteReviewRequest,
)
from app.domain.cfi_interview_chat import (
    CfiChatError,
    list_thread_messages,
    max_nudges,
    message_is_bot,
    message_sender_id,
    message_text,
    message_timestamp,
    nudge_interval_seconds,
    responders_for_tenant,
    send_message,
    space_for_tenant,
)
from app.domain.cfi_interview_models import (
    CfiInterviewMessageKind,
    CfiInterviewQuestion,
    CfiInterviewRecord,
    CfiInterviewReply,
    CfiInterviewResumeHandle,
    CfiInterviewStatus,
    CfiInterviewTickItem,
    CfiInterviewTickResult,
    CfiReplyClassification,
)
from app.domain.cfi_interview_reply import classify_reply, normalize_reply_text
from app.domain.cfi_interview_store import CfiInterviewConcurrencyError
from app.domain.cfi_launch_models import (
    CfiLaunchResult,
    CfiLaunchTickResult,
)
from app.domain.cfi_models import (
    CfiInvestigation,
    CfiLaunchStatus,
    SiblingBatchCandidate,
    SiblingBatchQueryResult,
)
from app.domain.cfi_report import build_cfi_report_body, build_cfi_report_title
from app.domain.cfi_report_writer import (
    CfiReportWriteError,
)
from app.domain.cfi_report_writer import (
    write_cfi_report as persist_cfi_report,
)
from app.domain.cfi_trend_models import CfiTrendQueryKind, CfiTrendResult
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
    get_sibling_batch_source,
)
from app.domain.qms_report_models import QmsReportWriteResult

__all__ = [
    "ALLOWED_DEPARTMENT_TOOLS",
    "CASE_MEMORY_TOOLS",
    "CASE_STATUS_TOOLS",
    "CASE_STATUS_UPSERT",
    "CFI_ARTIFACT_TOOLS",
    "CFI_CALENDAR_SITE_REVIEW",
    "CFI_GET",
    "CFI_INTERVIEW_TICK",
    "CFI_INTERVIEW_TOOLS",
    "CFI_LAUNCH_RESULT",
    "CFI_OPEN",
    "CFI_REPORT_WRITE",
    "CFI_SAVE",
    "CFI_SCHEDULER_TOOLS",
    "CFI_START_TICK",
    "CFI_TOOLS",
    "CFI_TREND_QUERY",
    "CHAT_READ_REPLIES",
    "CHAT_SEND_QUESTION",
    "COA_FETCH",
    "CONTROLLED_GUIDANCE_QUERY",
    "DRIVE_FETCH_EVIDENCE",
    "DRIVE_TOOLS",
    "EM_QUERY",
    "GUIDANCE_TOOLS",
    "LIMS_QUERY",
    "PRIOR_ART_QUERY",
    "PRIOR_ART_REDACT",
    "PRIOR_ART_WRITE",
    "QMS_REPORT_WRITE",
    "QMS_WRITE_TOOLS",
    "SIBLING_BATCH_QUERY",
    "SPONSOR_BATCH_STATUS",
    "SPONSOR_TOOLS",
    "STERILITY_BIND_RESUME_HANDLE",
    "STERILITY_GET",
    "STERILITY_START",
    "STERILITY_TOOLS",
    "CfiCalendarError",
    "CfiReportWriteError",
    "CfiTenantMismatch",
    "ControlledGuidanceTenantMismatch",
    "DepartmentRecordMissing",
    "DriveFetchError",
    "QmsReportWriteError",
    "SiblingBatchTenantMismatch",
    "case_status_upsert",
    "cfi_calendar_site_review",
    "cfi_get",
    "cfi_interview_tick",
    "cfi_launch_result",
    "cfi_open",
    "cfi_report_write",
    "cfi_start_tick",
    "cfi_trend_query",
    "chat_read_replies",
    "chat_send_question",
    "controlled_guidance_query",
    "drive_fetch_evidence",
    "em_query",
    "lims_query",
    "qms_report_write",
    "sibling_batch_query",
    "sponsor_batch_status",
]

LIMS_QUERY = "lims_query"
EM_QUERY = "em_query"
COA_FETCH = "coa_fetch"
DRIVE_FETCH_EVIDENCE = "drive_fetch_evidence"
STERILITY_START = "sterility_start"
STERILITY_GET = "sterility_get"

# Batch-keyed case status. case_status_upsert is written by the case
# workflow (sterility_wait.py, qp_confirmation.py) through this same MCP
# process (Cloud SQL credential boundary, same reasoning as the sterility
# clock). sponsor_batch_status is the one, separately scoped read the
# sponsor liaison service may perform — a different process, on the
# cross-company trust boundary, never called by the case workflow itself.
# Kept as two different tool names, not one tool with a role flag, so the
# case-workflow and sponsor-liaison Gateway allow-lists can never overlap
# by accident.
CASE_STATUS_UPSERT = "case_status_upsert"
SPONSOR_BATCH_STATUS = "sponsor_batch_status"
CASE_STATUS_TOOLS = frozenset({CASE_STATUS_UPSERT})
SPONSOR_TOOLS = frozenset({SPONSOR_BATCH_STATUS})

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

# Doc 12 T1-5: controlled-SOP and public-guidance RAG. Reached over MCP for
# the same reason as Case Memory and the sterility clock: Vertex AI RAG
# Engine calls run under the MCP process's own service-account credential,
# never under an Agent Identity principal directly. Read-only, its own tool
# name rather than reusing prior_art_query — a past-deviation fact and a
# controlled-guidance citation are different citation kinds with different
# gate requirements (see app/domain/case_models.py::CaseComplianceResult).
CONTROLLED_GUIDANCE_QUERY = "controlled_guidance_query"
GUIDANCE_TOOLS = frozenset({CONTROLLED_GUIDANCE_QUERY})

ALLOWED_DEPARTMENT_TOOLS = frozenset({LIMS_QUERY, EM_QUERY, COA_FETCH})

# drive_fetch_evidence takes a file_id, unlike the fixed
# tenant_id/batch_id/lifecycle_state/case_tenant_id shape
# call_department_tool hardcodes for the other three reads, so it is called
# through call_drive_tool with its own allow-list instead.
DRIVE_TOOLS = frozenset({DRIVE_FETCH_EVIDENCE})

# The only tool that mutates an external record (files/creates or overwrites
# a Google Doc). Kept in its own allow-list, never merged into
# ALLOWED_DEPARTMENT_TOOLS or DRIVE_TOOLS, so a Gateway allow-list bug that
# lets a read-only collector reach one of those sets can never also grant it
# write access to Drive.
QMS_REPORT_WRITE = "qms_report_write"
QMS_WRITE_TOOLS = frozenset({QMS_REPORT_WRITE})

# CFI (Cross-Functional Investigation): its own tool names and its own
# Gateway allow-list, disjoint from the case workflow's — a CFI Runtime may
# reach these; the case workflow never does (ARCHITECTURE.md section 7).
# ``cfi_get``/``cfi_save`` reach the Cloud SQL
# ``cfi_investigation`` table for the same credential-boundary reason as
# every other tool in this module. ``sibling_batch_query`` is read-only,
# tenant-locked, and reads across *batches* within one tenant only — never
# across tenants (guardrail 7 in the CFI plan).
CFI_GET = "cfi_get"
CFI_OPEN = "cfi_open"
CFI_SAVE = "cfi_save"

# CFI Phase 3 trend correlation. Read-only, tenant-locked exactly like
# sibling_batch_query: the model chooses which of three fixed comparisons to
# run (EM parameter, LIMS test, or this tenant's own prior-CFI recurrence
# count) and supplies no tenant scope, no raw SQL, and no table name.
CFI_TREND_QUERY = "cfi_trend_query"
CFI_CALENDAR_SITE_REVIEW = "cfi_calendar_site_review"
CFI_REPORT_WRITE = "cfi_report_write"
CFI_ARTIFACT_TOOLS = frozenset(
    {CFI_CALENDAR_SITE_REVIEW, CFI_REPORT_WRITE}
)
CFI_TOOLS = frozenset({CFI_GET, CFI_OPEN, CFI_SAVE, CFI_TREND_QUERY})

# CFI Phase 2 human interview tools. Chat credentials remain inside this
# department MCP process; the CFI Runtime receives only typed interview
# records over its own Gateway allow-list.
CHAT_SEND_QUESTION = "chat_send_question"
CHAT_READ_REPLIES = "chat_read_replies"
CFI_INTERVIEW_TICK = "cfi_interview_tick"
CFI_INTERVIEW_TOOLS = frozenset(
    {CHAT_SEND_QUESTION, CHAT_READ_REPLIES, CFI_INTERVIEW_TICK}
)

# Scheduler-only control-plane tools. They are intentionally not in
# CFI_TOOLS/CFI_INTERVIEW_TOOLS and are protected by the direct HTTP caller
# identity check in department_server.py.
CFI_START_TICK = "cfi_start_tick"
CFI_LAUNCH_RESULT = "cfi_launch_result"
CFI_SCHEDULER_TOOLS = frozenset(
    {CFI_INTERVIEW_TICK, CFI_START_TICK, CFI_LAUNCH_RESULT}
)

SIBLING_BATCH_QUERY = "sibling_batch_query"


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


class QmsReportWriteError(Exception):
    """The Docs/Drive API call failed, or the reports folder is unset."""


_QMS_REPORTS_FOLDER_ENV = "COUNTERSIGN_QMS_REPORTS_FOLDER_ID"
_QMS_TENANT_PROPERTY = "countersign_tenant_id"
_QMS_CASE_PROPERTY = "countersign_case_id"


def qms_report_write(
    *, tenant_id: str, case_id: str, title: str, body: str
) -> QmsReportWriteResult:
    """Files (or re-files) the investigation report + Annex 16 package as a
    real Google Doc in the ``03 Deviation Reports`` folder.

    Runs inside the MCP process, which holds the Drive/Docs credential — an
    Agent Identity principal cannot itself be a Drive/Docs OAuth or
    service-account principal, same reasoning as every other tool in this
    module. Idempotent by ``(tenant_id, case_id)``: a rerun (resume after a
    crash, or a corrected report) finds the existing filed doc by its Drive
    ``appProperties`` and overwrites its body in place rather than filing a
    second copy. ``title``/``body`` are pre-built deterministic text
    (``app.domain.qms_report``); this function does not draft content.
    """
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    from app.app_utils.services import get_google_credentials

    folder_id = os.environ.get(_QMS_REPORTS_FOLDER_ENV, "").strip()
    if not folder_id:
        raise QmsReportWriteError(
            f"{_QMS_REPORTS_FOLDER_ENV} is not set; cannot file the "
            "investigation report without a target Drive folder."
        )

    try:
        credentials = get_google_credentials(
            scopes=[
                "https://www.googleapis.com/auth/documents",
                "https://www.googleapis.com/auth/drive",
            ]
        )
        drive = build("drive", "v3", credentials=credentials)
        docs = build("docs", "v1", credentials=credentials)

        query = (
            f"'{folder_id}' in parents and trashed = false and "
            f"appProperties has {{key='{_QMS_TENANT_PROPERTY}' and "
            f"value='{tenant_id}'}} and "
            f"appProperties has {{key='{_QMS_CASE_PROPERTY}' and "
            f"value='{case_id}'}}"
        )
        existing = (
            drive.files()
            .list(q=query, fields="files(id,webViewLink)", pageSize=2)
            .execute()
            .get("files", [])
        )

        if existing:
            # Overwrite in place: a rerun after resume must not create a
            # second filed copy for the same case.
            document_id = existing[0]["id"]
            document = docs.documents().get(documentId=document_id).execute()
            end_index = document["body"]["content"][-1]["endIndex"]
            requests = []
            if end_index > 1:
                requests.append(
                    {
                        "deleteContentRange": {
                            "range": {"startIndex": 1, "endIndex": end_index - 1}
                        }
                    }
                )
            requests.append(
                {"insertText": {"location": {"index": 1}, "text": body}}
            )
            docs.documents().batchUpdate(
                documentId=document_id, body={"requests": requests}
            ).execute()
            drive.files().update(
                fileId=document_id, body={"name": title}
            ).execute()
            web_view_link = existing[0].get("webViewLink") or (
                f"https://docs.google.com/document/d/{document_id}/edit"
            )
            return QmsReportWriteResult(
                document_id=document_id,
                document_url=web_view_link,
                was_update=True,
            )

        created = docs.documents().create(body={"title": title}).execute()
        document_id = created["documentId"]
        docs.documents().batchUpdate(
            documentId=document_id,
            body={
                "requests": [
                    {"insertText": {"location": {"index": 1}, "text": body}}
                ]
            },
        ).execute()
        moved = (
            drive.files()
            .update(
                fileId=document_id,
                addParents=folder_id,
                body={
                    "appProperties": {
                        _QMS_TENANT_PROPERTY: tenant_id,
                        _QMS_CASE_PROPERTY: case_id,
                    }
                },
                fields="id,webViewLink",
            )
            .execute()
        )
        web_view_link = moved.get("webViewLink") or (
            f"https://docs.google.com/document/d/{document_id}/edit"
        )
        return QmsReportWriteResult(
            document_id=document_id,
            document_url=web_view_link,
            was_update=False,
        )
    except HttpError as exc:
        raise QmsReportWriteError(
            f"Docs/Drive API call for {tenant_id}/{case_id} failed: {exc}"
        ) from exc


class CaseStatusTenantMismatch(Exception):
    """The calling case is not the tenant that owns this batch's status."""


def _check_case_status_tenant(tenant_id: str, case_tenant_id: str) -> None:
    """Same discipline as ``_check_case_tenant``: the case workflow's own
    writes are tenant-checked here too, even though its caller is trusted
    infrastructure, not an external party. The sponsor liaison's own,
    separate cross-company check lives in ``sponsor_tools.py`` and is not
    this function — a sponsor never calls this tool.
    """
    if tenant_id != case_tenant_id:
        raise CaseStatusTenantMismatch(
            f"case_tenant_id {case_tenant_id!r} does not match "
            f"tenant_id {tenant_id!r}."
        )


def case_status_upsert(
    *,
    tenant_id: str,
    batch_id: str,
    case_id: str,
    status: str,
    updated_at: str,
    case_tenant_id: str,
    sterility_due_at: str | None = None,
    qp_confirmed_at: str | None = None,
):
    """Upsert one batch's status row. Called by the case workflow only.

    Runs inside the MCP process, which holds a real Cloud SQL IAM
    credential — same reasoning as ``sterility_start``.
    """
    _check_case_status_tenant(tenant_id, case_tenant_id)
    import datetime as _datetime

    from app.domain.case_status_models import CaseStatus, CaseStatusRecord
    from app.domain.case_status_store import get_case_status_store

    return get_case_status_store().upsert(
        CaseStatusRecord(
            tenant_id=tenant_id,
            batch_id=batch_id,
            case_id=case_id,
            status=CaseStatus(status),
            sterility_due_at=(
                _datetime.datetime.fromisoformat(sterility_due_at)
                if sterility_due_at
                else None
            ),
            qp_confirmed_at=(
                _datetime.datetime.fromisoformat(qp_confirmed_at)
                if qp_confirmed_at
                else None
            ),
            updated_at=_datetime.datetime.fromisoformat(updated_at),
        )
    )


def sponsor_batch_status(*, sponsor_id: str, tenant_id: str, batch_id: str):
    """The one read the sponsor liaison may perform. Not a bare status
    lookup: ``sponsor_tools.sponsor_batch_status`` enforces the
    sponsor-to-tenant Quality Agreement check *inside* this call, before
    touching the store — the same place every other tool in this module
    enforces its own scope check (``_check_case_tenant``,
    ``_check_memory_tenant``), rather than trusting a caller elsewhere to
    have checked first. A caller reaching this tool name with an
    unauthorized ``sponsor_id``/``tenant_id`` pair is denied here, not just
    upstream in the sponsor liaison service's own code.
    """
    from app.domain.sponsor_models import SponsorBatchStatusQuery
    from app.domain.sponsor_tools import sponsor_batch_status as run

    return run(
        SponsorBatchStatusQuery(
            sponsor_id=sponsor_id, tenant_id=tenant_id, batch_id=batch_id
        )
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
    hold_seconds: int | None = None,
):
    """Start (or return the existing) incubation clock. Idempotent.

    Runs inside the MCP process, which holds a real Cloud SQL IAM
    credential. An Agent Identity principal cannot be a Cloud SQL IAM
    database user (name/type constraints), so the Runtime workflow reaches
    this clock through this tool rather than opening Cloud SQL itself.

    ``hold_seconds`` carries the QA-specified, product-specific sterility
    duration from ``CaseIntake.sterility_hold_seconds``. There is no platform
    default: the store raises if neither this nor the demo-compression env
    var is set.
    """
    _check_case_tenant(tenant_id, case_tenant_id)
    from app.domain.sterility_store import get_sterility_store

    return get_sterility_store().start(
        tenant_id=tenant_id,
        batch_id=batch_id,
        case_id=case_id,
        workflow_version=workflow_version,
        hold_seconds=hold_seconds,
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
    root_cause_category: str | None = None,
):
    """Write one fact about a case into Case Memory for future retrieval.

    ``fact_id`` is optional; a caller with a natural idempotency key (see
    ``app/domain/case_memory_node.py::record_case_memory``) should pass one
    so a resume-triggered rerun overwrites the same fact instead of writing
    a duplicate. ``root_cause_category`` is the leading investigation
    hypothesis's fishbone tag at write time, if one was actually drafted for
    this case — never fabricated by this function.
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
        root_cause_category=root_cause_category,
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


class ControlledGuidanceTenantMismatch(Exception):
    """The calling case is not the tenant that owns this guidance query."""


def _check_guidance_tenant(tenant_id: str, case_tenant_id: str) -> None:
    if tenant_id != case_tenant_id:
        raise ControlledGuidanceTenantMismatch(
            f"case_tenant_id {case_tenant_id!r} does not match "
            f"tenant_id {tenant_id!r}."
        )


class CfiTenantMismatch(Exception):
    """The CFI record is not owned by the calling case tenant."""


class SiblingBatchTenantMismatch(Exception):
    """The calling CFI is not the tenant that owns the batches queried."""


def _check_sibling_batch_tenant(tenant_id: str, case_tenant_id: str) -> None:
    """Same discipline as every other tool's tenant check in this module.

    Scope expansion may cross *batches* within the CFI's own tenant, never
    across tenants (CFI plan guardrail 7). The tenant filter is applied a
    second time, inside ``list_department_snapshots_for_tenant`` itself, so
    this check and that filter both have to agree before any row can leave
    this function — not merely trusting the caller checked once upstream.
    """
    if tenant_id != case_tenant_id:
        raise SiblingBatchTenantMismatch(
            f"case_tenant_id {case_tenant_id!r} does not match "
            f"tenant_id {tenant_id!r}."
        )


def sibling_batch_query(
    *,
    tenant_id: str,
    batch_id: str,
    shared_attribute: str,
    case_tenant_id: str,
) -> SiblingBatchQueryResult:
    """Batches on this tenant sharing ``shared_attribute`` with ``batch_id``.

    Read-only, tenant-locked exactly like ``lims_query``/``em_query``: the
    model supplies only which attribute to compare on (currently
    ``"material_lot"``, the CoA field GMPSOP QMS-130 section 4.3 names
    explicitly — "shared raw material lots"), never the tenant scope, which
    is stamped by the caller and re-checked here.

    Returns an **association only** — a shared lot or shared equipment
    across batches is a lead for a human investigator, never itself a
    determination of a common root cause (CFI plan section 10).

    Raises:
        SiblingBatchTenantMismatch: ``case_tenant_id`` does not match
            ``tenant_id``.
        ValueError: ``shared_attribute`` is not a supported comparison
            attribute, or the queried batch itself is not in the catalog.
    """
    _check_sibling_batch_tenant(tenant_id, case_tenant_id)
    if shared_attribute != "material_lot":
        raise ValueError(
            f"Unsupported shared_attribute {shared_attribute!r}; "
            "Phase 1 supports only 'material_lot'."
        )

    try:
        _source_lot, matches = get_sibling_batch_source().batches_sharing_material_lot(
            tenant_id=tenant_id, batch_id=batch_id
        )
    except DepartmentRecordMissing as exc:
        raise ValueError(str(exc)) from exc

    candidates = [
        SiblingBatchCandidate(
            batch_id=match_batch_id,
            shared_attribute="material_lot",
            shared_value=material_lot,
            source_uri=source_uri,
        )
        for match_batch_id, material_lot, source_uri in matches
    ]
    return SiblingBatchQueryResult(
        tenant_id=tenant_id,
        batch_id=batch_id,
        shared_attribute=shared_attribute,
        candidates=candidates,
    )


def _check_cfi_tenant(tenant_id: str, case_tenant_id: str) -> None:
    """Bind every CFI store operation to the authenticated case tenant."""
    if tenant_id != case_tenant_id:
        raise CfiTenantMismatch(
            f"case_tenant_id {case_tenant_id!r} does not match "
            f"tenant_id {tenant_id!r}."
        )


def cfi_open(
    *, tenant_id: str, case_tenant_id: str, investigation: dict
) -> CfiInvestigation:
    """Atomically open or reuse one CFI for a tenant/source case.

    The store's unique ``(tenant_id, source_case_id)`` key is the actual
    idempotency boundary; this function only supplies the MCP-side tenant
    check and schema validation before the write.
    """
    _check_cfi_tenant(tenant_id, case_tenant_id)
    from app.domain.cfi_store import get_cfi_store

    record = CfiInvestigation.model_validate(investigation)
    if record.tenant_id != tenant_id:
        raise CfiTenantMismatch(
            f"investigation tenant_id {record.tenant_id!r} does not match "
            f"tenant_id {tenant_id!r}."
        )
    return get_cfi_store().open(record)


def cfi_get(
    *, tenant_id: str, case_tenant_id: str, cfi_id: str
) -> CfiInvestigation | None:
    """Read one CFI only when its stored tenant matches the caller."""
    _check_cfi_tenant(tenant_id, case_tenant_id)
    from app.domain.cfi_store import get_cfi_store

    record = get_cfi_store().get(cfi_id)
    if record is not None and record.tenant_id != tenant_id:
        raise CfiTenantMismatch(
            f"CFI {cfi_id!r} is not owned by tenant {tenant_id!r}."
        )
    return record


def cfi_save(
    *, tenant_id: str, case_tenant_id: str, investigation: dict
) -> CfiInvestigation:
    """Optimistically save one tenant-bound CFI aggregate by revision."""
    _check_cfi_tenant(tenant_id, case_tenant_id)
    from app.domain.cfi_store import get_cfi_store

    record = CfiInvestigation.model_validate(investigation)
    if record.tenant_id != tenant_id:
        raise CfiTenantMismatch(
            f"investigation tenant_id {record.tenant_id!r} does not match "
            f"tenant_id {tenant_id!r}."
        )
    return get_cfi_store().save(record)


def cfi_trend_query(
    *,
    tenant_id: str,
    batch_id: str,
    cfi_id: str,
    case_tenant_id: str,
    kind: str,
    compare_batch_id: str | None = None,
    parameter: str | None = None,
    test_code: str | None = None,
) -> CfiTrendResult:
    """One of three fixed, tenant-locked CFI trend comparisons.

    Read-only, same tenant-check discipline as ``sibling_batch_query``. The
    model chooses only ``kind``/``compare_batch_id``/``parameter``/
    ``test_code``; ``tenant_id`` is stamped by the caller and re-checked
    here. There is no complaint or Annual Product Review source in this
    deployment (2026-08-25 read-only catalog discovery), so no such kind
    exists — this function never fabricates a comparison it cannot back
    with a real query.

    Raises:
        CfiTenantMismatch: ``case_tenant_id`` does not match ``tenant_id``.
        ValueError: an unsupported ``kind``, a missing required argument for
            the chosen ``kind``, or a compared batch/test/parameter that has
            no matching row in the backing system.
    """
    _check_cfi_tenant(tenant_id, case_tenant_id)
    try:
        query_kind = CfiTrendQueryKind(kind)
    except ValueError as exc:
        raise ValueError(f"Unsupported cfi_trend_query kind {kind!r}.") from exc

    if query_kind is CfiTrendQueryKind.EM_PARAMETER_COMPARISON:
        if not compare_batch_id or not parameter:
            raise ValueError(
                "em_parameter_comparison requires compare_batch_id and parameter."
            )
        source = get_em_source()
        try:
            subject = source.em(tenant_id=tenant_id, batch_id=batch_id)
            compare = source.em(tenant_id=tenant_id, batch_id=compare_batch_id)
        except DepartmentRecordMissing as exc:
            raise ValueError(str(exc)) from exc
        subject_values = [
            row.value for row in subject.rows if row.parameter == parameter
        ]
        compare_values = [
            row.value for row in compare.rows if row.parameter == parameter
        ]
        if not subject_values or not compare_values:
            raise ValueError(
                f"No EM readings for parameter {parameter!r} on one or both "
                f"of {batch_id!r}/{compare_batch_id!r}."
            )
        subject_mean = sum(subject_values) / len(subject_values)
        compare_mean = sum(compare_values) / len(compare_values)
        unit = next(
            (row.unit for row in subject.rows if row.parameter == parameter), None
        )
        return CfiTrendResult(
            kind=query_kind,
            tenant_id=tenant_id,
            batch_id=batch_id,
            compare_batch_id=compare_batch_id,
            parameter_or_test_code=parameter,
            subject_mean=subject_mean,
            compare_mean=compare_mean,
            unit=unit,
            delta=subject_mean - compare_mean,
            source_uri=subject.source_uri,
        )

    if query_kind is CfiTrendQueryKind.LIMS_TEST_COMPARISON:
        if not compare_batch_id or not test_code:
            raise ValueError(
                "lims_test_comparison requires compare_batch_id and test_code."
            )
        source = get_lims_source()
        try:
            subject = source.lims(tenant_id=tenant_id, batch_id=batch_id)
            compare = source.lims(tenant_id=tenant_id, batch_id=compare_batch_id)
        except DepartmentRecordMissing as exc:
            raise ValueError(str(exc)) from exc
        subject_values = [
            row.result_value for row in subject.rows if row.test_code == test_code
        ]
        compare_values = [
            row.result_value for row in compare.rows if row.test_code == test_code
        ]
        if not subject_values or not compare_values:
            raise ValueError(
                f"No LIMS results for test_code {test_code!r} on one or "
                f"both of {batch_id!r}/{compare_batch_id!r}."
            )
        subject_mean = sum(subject_values) / len(subject_values)
        compare_mean = sum(compare_values) / len(compare_values)
        unit = next(
            (row.unit for row in subject.rows if row.test_code == test_code), None
        )
        return CfiTrendResult(
            kind=query_kind,
            tenant_id=tenant_id,
            batch_id=batch_id,
            compare_batch_id=compare_batch_id,
            parameter_or_test_code=test_code,
            subject_mean=subject_mean,
            compare_mean=compare_mean,
            unit=unit,
            delta=subject_mean - compare_mean,
            source_uri=subject.source_uri,
        )

    # PRIOR_CFI_RECURRENCE
    from app.domain.cfi_store import get_cfi_store

    prior_count = get_cfi_store().count_for_tenant(
        tenant_id=tenant_id, exclude_cfi_id=cfi_id
    )
    return CfiTrendResult(
        kind=query_kind,
        tenant_id=tenant_id,
        batch_id=batch_id,
        prior_cfi_count=prior_count,
        source_uri=f"cfi_investigation://{tenant_id}?exclude={cfi_id}",
    )


def _closed_cfi_for_tenant(
    *, tenant_id: str, case_tenant_id: str, cfi_id: str
) -> CfiInvestigation:
    _check_cfi_tenant(tenant_id, case_tenant_id)
    from app.domain.cfi_store import get_cfi_store

    record = get_cfi_store().get(cfi_id)
    if record is None:
        raise ValueError(f"No CFI exists for cfi_id={cfi_id!r}.")
    if record.tenant_id != tenant_id:
        raise CfiTenantMismatch(
            f"CFI {cfi_id!r} is not owned by tenant {tenant_id!r}."
        )
    if record.outcome is None or record.closed_at is None:
        raise ValueError("CFI artifacts may be filed only after deterministic closure.")
    return record


def _cfi_artifact_record(
    *,
    tenant_id: str,
    case_tenant_id: str,
    cfi_id: str,
    source_case_id: str,
    batch_id: str,
    outcome: str,
) -> CfiInvestigation:
    """Validate an artifact request against the durable CFI aggregate."""
    record = _closed_cfi_for_tenant(
        tenant_id=tenant_id, case_tenant_id=case_tenant_id, cfi_id=cfi_id
    )
    if record.source_case_id != source_case_id or record.batch_id != batch_id:
        raise ValueError(
            "CFI artifact identity does not match the durable source case or batch."
        )
    if record.outcome is None or record.closed_at is None:
        raise ValueError("CFI artifacts may be filed only after deterministic closure.")
    if record.outcome.value != outcome:
        raise ValueError(
            f"Artifact outcome {outcome!r} does not match the durable CFI outcome "
            f"{record.outcome.value!r}."
        )
    return record


def cfi_calendar_site_review(
    *,
    tenant_id: str,
    case_tenant_id: str,
    cfi_id: str,
    source_case_id: str,
    batch_id: str,
    outcome: str,
    closed_at: str,
) -> CfiSiteReviewReference:
    """Schedule the tenant-configured, deterministic CFI site-review event."""
    request = CfiSiteReviewRequest.model_validate(
        {
            "tenant_id": tenant_id,
            "case_tenant_id": case_tenant_id,
            "cfi_id": cfi_id,
            "source_case_id": source_case_id,
            "batch_id": batch_id,
            "outcome": outcome,
            "closed_at": closed_at,
        }
    )
    record = _cfi_artifact_record(
        tenant_id=request.tenant_id,
        case_tenant_id=request.case_tenant_id,
        cfi_id=request.cfi_id,
        source_case_id=request.source_case_id,
        batch_id=request.batch_id,
        outcome=request.outcome,
    )
    if record.closed_at != request.closed_at:
        raise ValueError("closed_at does not match the durable CFI close time.")
    return persist_site_review(
        tenant_id=record.tenant_id,
        cfi_id=record.cfi_id,
        source_case_id=record.source_case_id,
        batch_id=record.batch_id,
        outcome=record.outcome.value,
        closed_at=record.closed_at,
    )


def cfi_report_write(
    *,
    tenant_id: str,
    case_tenant_id: str,
    cfi_id: str,
    title: str,
    body: str,
) -> CfiReportWriteResult:
    """File only the deterministic report for the durable closed CFI."""
    request = CfiReportWriteRequest.model_validate(
        {
            "tenant_id": tenant_id,
            "case_tenant_id": case_tenant_id,
            "cfi_id": cfi_id,
            "title": title,
            "body": body,
        }
    )
    record = _closed_cfi_for_tenant(
        tenant_id=request.tenant_id,
        case_tenant_id=request.case_tenant_id,
        cfi_id=request.cfi_id,
    )
    expected_title = build_cfi_report_title(record)
    expected_body = build_cfi_report_body(record)
    if request.title != expected_title or request.body != expected_body:
        raise CfiReportWriteError(
            "CFI report content does not match the deterministic durable CFI "
            "record; model-authored report text is not accepted."
        )
    return persist_cfi_report(
        tenant_id=record.tenant_id,
        cfi_id=record.cfi_id,
        title=expected_title,
        body=expected_body,
    )


def cfi_start_tick(
    *,
    now: str,
    lease_seconds: int,
    max_starts: int,
    max_attempts: int,
) -> CfiLaunchTickResult:
    """Claim bounded pending CFI Runtime starts for the scheduler.

    This is a scheduler-only control-plane operation. It returns only typed
    CFI pointers and never the durable investigation payload. Cloud SQL
    ``FOR UPDATE SKIP LOCKED``/the equivalent local test lock makes two
    overlapping ticks unable to claim the same launch lease.
    """
    try:
        stamp = datetime.datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"now is not an RFC-3339 datetime: {now!r}.") from exc
    if stamp.tzinfo is None:
        raise ValueError("now must include a timezone.")
    if not 1 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 1 and 3600.")
    if not 1 <= max_starts <= 100:
        raise ValueError("max_starts must be between 1 and 100.")
    if not 1 <= max_attempts <= 10:
        raise ValueError("max_attempts must be between 1 and 10.")

    from app.domain.cfi_store import get_cfi_store

    return CfiLaunchTickResult(
        items=get_cfi_store().claim_pending_starts(
            now=stamp.astimezone(datetime.UTC),
            lease_seconds=lease_seconds,
            limit=max_starts,
            max_attempts=max_attempts,
        )
    )


def cfi_launch_result(
    *,
    tenant_id: str,
    case_tenant_id: str,
    cfi_id: str,
    launch_status: str,
    runtime_invocation_id: str | None = None,
    launch_error: str | None = None,
) -> CfiLaunchResult:
    """Persist one scheduler launch result, tenant-locked and idempotent."""
    _check_cfi_tenant(tenant_id, case_tenant_id)
    try:
        status = CfiLaunchStatus(launch_status)
    except ValueError as exc:
        raise ValueError(f"Unsupported CFI launch status {launch_status!r}.") from exc
    from app.domain.cfi_store import get_cfi_store

    store = get_cfi_store()
    if status is CfiLaunchStatus.STARTED:
        if not runtime_invocation_id:
            raise ValueError("started launch result requires runtime_invocation_id.")
        item = store.mark_launch_started(
            tenant_id=tenant_id,
            cfi_id=cfi_id,
            invocation_id=runtime_invocation_id,
        )
    elif status is CfiLaunchStatus.FAILED:
        if not launch_error or not launch_error.strip():
            raise ValueError("failed launch result requires launch_error.")
        item = store.mark_launch_failed(
            tenant_id=tenant_id,
            cfi_id=cfi_id,
            error=launch_error,
        )
    else:
        raise ValueError(
            "Scheduler may record only 'started' or 'failed' launch results."
        )
    return CfiLaunchResult(
        cfi_id=item.cfi_id,
        tenant_id=item.tenant_id,
        launch_status=item.launch_status,
        runtime_invocation_id=runtime_invocation_id
        if status is CfiLaunchStatus.STARTED
        else None,
        launch_error=launch_error if status is CfiLaunchStatus.FAILED else None,
    )


class CfiInterviewTenantMismatch(Exception):
    """The interview is not owned by the calling tenant."""


class CfiInterviewScopeError(Exception):
    """The interview resource or Chat correlation is invalid."""


def _check_cfi_interview_tenant(tenant_id: str, case_tenant_id: str) -> None:
    if tenant_id != case_tenant_id:
        raise CfiInterviewTenantMismatch(
            f"case_tenant_id {case_tenant_id!r} does not match "
            f"tenant_id {tenant_id!r}."
        )


def _parse_interview_time(value: str) -> datetime.datetime:
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CfiInterviewScopeError(
            f"deadline_at is not an RFC-3339 datetime: {value!r}."
        ) from exc
    if parsed.tzinfo is None:
        raise CfiInterviewScopeError("deadline_at must include a timezone.")
    return parsed.astimezone(datetime.UTC)


def _interview_thread_key(interview_id: str) -> str:
    return "cfi-" + hashlib.sha256(interview_id.encode("utf-8")).hexdigest()[:32]


def _resume_handle(
    *,
    app_name: str | None,
    user_id: str | None,
    session_id: str | None,
    invocation_id: str | None,
    interrupt_id: str | None,
) -> CfiInterviewResumeHandle | None:
    values = (app_name, user_id, session_id, invocation_id, interrupt_id)
    if all(value and value.strip() for value in values):
        return CfiInterviewResumeHandle(
            app_name=str(app_name),
            user_id=str(user_id),
            session_id=str(session_id),
            invocation_id=str(invocation_id),
            interrupt_id=str(interrupt_id),
        )
    if any(value for value in values):
        raise CfiInterviewScopeError(
            "Interview resume handle must provide all five fields together."
        )
    return None


def chat_send_question(
    *,
    tenant_id: str,
    case_tenant_id: str,
    interview_id: str,
    cfi_id: str,
    hypothesis_id: str,
    round_number: int,
    question_number: int,
    question_text: str,
    deadline_at: str,
    app_name: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    invocation_id: str | None = None,
    interrupt_id: str | None = None,
    is_nudge: bool = False,
) -> CfiInterviewRecord:
    """Create one idempotent CFI Chat question or bounded nudge.

    The caller never supplies a Chat space or responder list. Those are
    resolved from server-owned tenant configuration and persisted with the
    interview. The deterministic graph supplies the resume handle; the
    model supplies only ``question_text``.
    """
    _check_cfi_interview_tenant(tenant_id, case_tenant_id)
    from app.domain.cfi_interview_store import get_cfi_interview_store

    store = get_cfi_interview_store()
    record = store.get(interview_id)
    now = datetime.datetime.now(datetime.UTC)
    parsed_deadline = _parse_interview_time(deadline_at)
    configured_space = space_for_tenant(tenant_id)
    configured_responders = responders_for_tenant(tenant_id)
    configured_max_nudges = max_nudges()
    handle = _resume_handle(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        invocation_id=invocation_id,
        interrupt_id=interrupt_id,
    )

    if record is None:
        record = store.open(
            CfiInterviewRecord(
                interview_id=interview_id,
                tenant_id=tenant_id,
                cfi_id=cfi_id,
                hypothesis_id=hypothesis_id,
                round_number=round_number,
                status=CfiInterviewStatus.QUESTION_SEND_PENDING,
                allowed_responder_ids=list(configured_responders),
                space_name=configured_space,
                deadline_at=parsed_deadline,
                max_nudges=configured_max_nudges,
                resume_handle=handle,
            )
        )
    else:
        if (
            record.tenant_id != tenant_id
            or record.cfi_id != cfi_id
            or record.hypothesis_id != hypothesis_id
            or record.round_number != round_number
        ):
            raise CfiInterviewScopeError(
                "Interview identity does not match the stored tenant/CFI/round."
            )
        if record.space_name != configured_space:
            raise CfiInterviewScopeError(
                "Stored interview space does not match server tenant configuration."
            )
        if tuple(record.allowed_responder_ids) != configured_responders:
            raise CfiInterviewScopeError(
                "Stored responder allow-list differs from current tenant configuration."
            )
        if handle is not None:
            record = record.model_copy(update={"resume_handle": handle})

    kind = CfiInterviewMessageKind.NUDGE if is_nudge else CfiInterviewMessageKind.QUESTION
    existing = next(
        (
            item
            for item in record.questions
            if item.kind is kind and item.question_number == question_number
        ),
        None,
    )
    if existing is not None:
        if existing.question_text != question_text:
            raise CfiInterviewScopeError(
                "An idempotency key was reused with different question text."
            )
        return record

    if record.status in {
        CfiInterviewStatus.COMPLETED,
        CfiInterviewStatus.INTERVIEW_UNANSWERED,
        CfiInterviewStatus.FAILED,
    }:
        raise CfiInterviewScopeError(
            f"Cannot send a new interview message in terminal status "
            f"{record.status.value!r}."
        )
    if not question_text.strip():
        raise CfiInterviewScopeError("Question text must not be empty.")
    if not is_nudge and question_number != len(
        [item for item in record.questions if item.kind is CfiInterviewMessageKind.QUESTION]
    ) + 1:
        raise CfiInterviewScopeError(
            "Question numbers must advance exactly once per interview question."
        )
    if is_nudge:
        if record.nudge_count >= record.max_nudges:
            return record
        if record.last_nudge_at is not None and (
            now - record.last_nudge_at
        ).total_seconds() < nudge_interval_seconds():
            return record

    action_key = (
        f"{interview_id}:nudge:{record.nudge_count + 1}"
        if is_nudge
        else f"{interview_id}:question:{question_number}"
    )
    response = send_message(
        space_name=record.space_name or configured_space,
        thread_key=(
            None if record.thread_name else _interview_thread_key(interview_id)
        ),
        thread_name=record.thread_name,
        text=question_text,
        action_key=action_key,
    )
    message_name = response.get("name")
    response_thread = (response.get("thread") or {}).get("name")
    if not isinstance(message_name, str) or not message_name:
        raise CfiChatError("Google Chat response did not contain message.name.")
    if not isinstance(response_thread, str) or not response_thread:
        raise CfiChatError("Google Chat response did not contain thread.name.")
    response_space = response_thread.rsplit("/threads/", 1)[0]
    if response_space != (record.space_name or configured_space):
        raise CfiChatError("Google Chat returned a thread in an unexpected space.")
    sent_at = message_timestamp(response) or now
    questions = [
        *record.questions,
        CfiInterviewQuestion(
            question_number=question_number,
            kind=kind,
            question_text=question_text,
            sent_at=sent_at,
            message_name=message_name,
            thread_name=response_thread,
            space_name=record.space_name or configured_space,
        ),
    ]
    updated = record.model_copy(
        update={
            "status": CfiInterviewStatus.WAITING_FOR_REPLY,
            "space_name": record.space_name or configured_space,
            "thread_name": response_thread,
            "questions": questions,
            "nudge_count": record.nudge_count + (1 if is_nudge else 0),
            "last_nudge_at": now if is_nudge else record.last_nudge_at,
            "poll_lease_until": None,
            "resume_handle": handle or record.resume_handle,
        }
    )
    return store.save(updated)


def chat_read_replies(
    *,
    tenant_id: str,
    case_tenant_id: str,
    interview_id: str,
    finalize: bool = False,
) -> CfiInterviewRecord:
    """Read and persist only new messages in the stored interview thread.

    The Chat API result is treated as untrusted input. Sender authorization,
    exact-thread correlation, deterministic classification, cursor advance,
    deadline handling, and state transitions all happen here before a reply
    crosses the MCP boundary.
    """
    _check_cfi_interview_tenant(tenant_id, case_tenant_id)
    from app.domain.cfi_interview_store import get_cfi_interview_store

    store = get_cfi_interview_store()
    record = store.get(interview_id)
    if record is None:
        raise CfiInterviewScopeError(f"No CFI interview exists for {interview_id!r}.")
    if record.tenant_id != tenant_id:
        raise CfiInterviewTenantMismatch(
            f"Interview {interview_id!r} is not owned by tenant {tenant_id!r}."
        )
    if record.status is CfiInterviewStatus.COMPLETED:
        return record
    if record.status is CfiInterviewStatus.INTERVIEW_UNANSWERED and not finalize:
        return record
    if not record.space_name or not record.thread_name:
        raise CfiInterviewScopeError("Interview has no persisted Chat thread.")

    now = datetime.datetime.now(datetime.UTC)
    messages = list_thread_messages(
        space_name=record.space_name,
        thread_name=record.thread_name,
        after=record.last_seen_message_time,
    )
    known = {item.message_name for item in record.replies}
    known.update(item.message_name for item in record.questions if item.message_name)
    replies = list(record.replies)
    latest_time = record.last_seen_message_time
    latest_name = record.last_seen_message_name
    question_number = max(
        (item.question_number for item in record.questions), default=1
    )
    for message in messages:
        message_thread = (message.get("thread") or {}).get("name")
        if message_thread != record.thread_name:
            raise CfiChatError(
                "Google Chat returned a message outside the persisted interview thread."
            )
        message_name = message.get("name")
        if not isinstance(message_name, str) or not message_name:
            continue
        stamp = message_timestamp(message) or now
        if latest_time is None or stamp > latest_time:
            latest_time = stamp
            latest_name = message_name
        elif stamp == latest_time and (latest_name is None or message_name > latest_name):
            latest_name = message_name
        if message_name in known or message_is_bot(message):
            continue
        sender_id = message_sender_id(message)
        if sender_id is None:
            continue
        raw_text = message_text(message)
        sender_allowed = sender_id in record.allowed_responder_ids
        classification = (
            classify_reply(raw_text)
            if sender_allowed
            else CfiReplyClassification.OUT_OF_SCOPE
        )
        replies.append(
            CfiInterviewReply(
                message_name=message_name,
                sender_id=sender_id,
                raw_text=raw_text,
                received_at=stamp,
                classification=classification,
                sender_allowed=sender_allowed,
                question_number=question_number,
            )
        )
        known.add(message_name)

    actionable = next(
        (
            reply
            for reply in replies
            if reply.sender_allowed
            and reply.classification
            in {
                CfiReplyClassification.ANSWER,
                CfiReplyClassification.DECLINE,
                CfiReplyClassification.STOP,
            }
        ),
        None,
    )
    if actionable is not None:
        status = (
            CfiInterviewStatus.COMPLETED
            if finalize
            else CfiInterviewStatus.REPLY_CAPTURED
        )
        summary = normalize_reply_text(actionable.raw_text)[:1000] or (
            actionable.classification.value
        )
    elif now >= record.deadline_at:
        status = CfiInterviewStatus.INTERVIEW_UNANSWERED
        summary = "No accepted human answer was received before the interview deadline."
    else:
        status = CfiInterviewStatus.WAITING_FOR_REPLY
        summary = record.outcome_summary

    changed = (
        replies != record.replies
        or latest_time != record.last_seen_message_time
        or latest_name != record.last_seen_message_name
        or status != record.status
        or record.poll_lease_until is not None
        or (finalize and status is CfiInterviewStatus.COMPLETED)
    )
    if not changed:
        return record
    updated = record.model_copy(
        update={
            "status": status,
            "replies": replies,
            "last_seen_message_time": latest_time,
            "last_seen_message_name": latest_name,
            "poll_lease_until": None,
            "outcome_summary": summary,
        }
    )
    return store.save(updated)


def cfi_interview_tick(
    *,
    now: str,
    lease_seconds: int,
    max_interviews: int,
) -> CfiInterviewTickResult:
    """Claim and poll a bounded batch of production CFI interviews.

    This is an internal scheduler operation: Cloud SQL and Google Chat stay
    inside the department MCP, while the scheduler receives only the minimal
    resume metadata needed to wake the deployed CFI Runtime. Raw human reply
    text never crosses this boundary.
    """
    try:
        tick_time = datetime.datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CfiInterviewScopeError(
            f"now is not an RFC-3339 datetime: {now!r}."
        ) from exc
    if tick_time.tzinfo is None:
        raise CfiInterviewScopeError("now must include a timezone.")
    tick_time = tick_time.astimezone(datetime.UTC)
    if lease_seconds < 1 or lease_seconds > 3600:
        raise CfiInterviewScopeError("lease_seconds must be between 1 and 3600.")
    if max_interviews < 1 or max_interviews > 100:
        raise CfiInterviewScopeError("max_interviews must be between 1 and 100.")

    from app.domain.cfi_interview_store import get_cfi_interview_store

    store = get_cfi_interview_store()
    claimed = store.claim_waiting(
        now=tick_time, lease_seconds=lease_seconds, limit=max_interviews
    )
    items: list[CfiInterviewTickItem] = []
    for claimed_record in claimed:
        try:
            record = chat_read_replies(
                tenant_id=claimed_record.tenant_id,
                case_tenant_id=claimed_record.tenant_id,
                interview_id=claimed_record.interview_id,
                finalize=False,
            )
            nudge_sent = False
            if (
                record.status is CfiInterviewStatus.WAITING_FOR_REPLY
                and record.questions
                and record.nudge_count < record.max_nudges
            ):
                handle = record.resume_handle
                nudged = chat_send_question(
                    tenant_id=record.tenant_id,
                    case_tenant_id=record.tenant_id,
                    interview_id=record.interview_id,
                    cfi_id=record.cfi_id,
                    hypothesis_id=record.hypothesis_id,
                    round_number=record.round_number,
                    question_number=record.questions[-1].question_number,
                    question_text=(
                        "Reminder: please reply to the CFI question in this "
                        "thread with observed facts or records. Reply "
                        "'decline' or 'stop' if you cannot continue. Do not "
                        "include credentials or other secrets."
                    ),
                    deadline_at=record.deadline_at.isoformat(),
                    app_name=handle.app_name if handle else None,
                    user_id=handle.user_id if handle else None,
                    session_id=handle.session_id if handle else None,
                    invocation_id=handle.invocation_id if handle else None,
                    interrupt_id=handle.interrupt_id if handle else None,
                    is_nudge=True,
                )
                nudge_sent = nudged.nudge_count > record.nudge_count
                record = nudged

            if record.status in {
                CfiInterviewStatus.REPLY_CAPTURED,
                CfiInterviewStatus.INTERVIEW_UNANSWERED,
            }:
                reason = "resume_required"
            elif nudge_sent:
                reason = "nudge_sent"
            else:
                reason = "waiting_for_reply"
            items.append(
                CfiInterviewTickItem(
                    tenant_id=record.tenant_id,
                    interview_id=record.interview_id,
                    status=record.status,
                    nudge_sent=nudge_sent,
                    nudge_count=record.nudge_count,
                    resume_handle=record.resume_handle
                    if record.status
                    in {
                        CfiInterviewStatus.REPLY_CAPTURED,
                        CfiInterviewStatus.INTERVIEW_UNANSWERED,
                    }
                    else None,
                    reason=reason,
                )
            )
        except (
            CfiChatError,
            CfiInterviewConcurrencyError,
            CfiInterviewScopeError,
            CfiInterviewTenantMismatch,
        ) as exc:
            released = store.release_poll(claimed_record.interview_id)
            items.append(
                CfiInterviewTickItem(
                    tenant_id=claimed_record.tenant_id,
                    interview_id=claimed_record.interview_id,
                    status=(
                        released.status
                        if released is not None
                        else CfiInterviewStatus.FAILED
                    ),
                    nudge_count=released.nudge_count if released else 0,
                    resume_handle=None,
                    reason=f"poll_failed: {exc}",
                )
            )
    return CfiInterviewTickResult(items=items)
async def controlled_guidance_query(
    *, tenant_id: str, corpus: str, query: str, case_tenant_id: str, top_k: int = 5
):
    """Doc 12 T1-5: tenant-scoped, manifest-verified SOP/public-guidance
    retrieval. Advisory input for Investigation only.

    Runs inside the MCP process, which holds the real Vertex AI credential
    — an Agent Identity principal cannot itself be a Vertex AI RAG Engine
    caller any more than it can be a Cloud SQL or Firestore one.
    """
    _check_guidance_tenant(tenant_id, case_tenant_id)
    from app.domain.guidance_models import GuidanceCorpus, GuidanceQueryResult
    from app.domain.guidance_source import get_guidance_source

    citations, configured = await get_guidance_source().query(
        tenant_id=tenant_id,
        corpus=GuidanceCorpus(corpus),
        query=query,
        top_k=top_k,
    )
    return GuidanceQueryResult(
        tenant_id=tenant_id, query=query, citations=citations, configured=configured
    )
