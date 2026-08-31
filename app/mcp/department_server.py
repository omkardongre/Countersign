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

"""Streamable-HTTP MCP server for LIMS / EM / CoA reads and the sterility clock.

One process, six exact tool names: ``lims_query``, ``em_query``, ``coa_fetch``,
and ``sterility_start`` / ``sterility_get`` / ``sterility_bind_resume_handle``.
The sterility tools exist because an Agent Identity principal cannot be a
Cloud SQL IAM database user (confirmed against the live instance), so the
case Runtime reaches the clock through this service — which runs with a real
service-account credential — instead of opening Cloud SQL itself.

Gateway policies (not this process) decide which Agent Identity may call
which name. stdio is not used: Agent Gateway needs Streamable HTTP.
postgres-execute-sql is not registered.

Pinned to PyPI ``mcp`` FastMCP (the API Agent Runtime already ships).
"""

from __future__ import annotations

import functools
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

import google.auth.transport.requests
from google.oauth2 import id_token
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.domain.capa_models import (
    NonConformanceWriteResult,
    QualityActionWriteResult,
)
from app.domain.case_memory_models import (
    CaseMemoryFact,
    CaseMemoryQueryResult,
)
from app.domain.case_status_models import CaseStatusRecord
from app.domain.cfi_artifact_models import (
    CfiReportWriteResult,
    CfiSiteReviewReference,
)
from app.domain.cfi_interview_chat import CfiChatError
from app.domain.cfi_interview_models import (
    CfiInterviewRecord,
    CfiInterviewTickResult,
)
from app.domain.cfi_interview_store import CfiInterviewConcurrencyError
from app.domain.cfi_launch_models import (
    CfiLaunchResult,
    CfiLaunchTickResult,
)
from app.domain.cfi_models import CfiInvestigation, SiblingBatchQueryResult
from app.domain.cfi_store import (
    CfiConcurrencyError,
    CfiNotFoundError,
)
from app.domain.cfi_trend_models import CfiTrendResult
from app.domain.department_models import (
    CoaSnapshot,
    DriveEvidencePayload,
    EmSnapshot,
    LimsSnapshot,
    MaintenanceSnapshot,
)
from app.domain.department_source_erpnext import ERPNextApiError
from app.domain.department_source_openmaint import OpenMaintApiError
from app.domain.department_tools import (
    CASE_STATUS_UPSERT,
    CFI_CALENDAR_SITE_REVIEW,
    CFI_GET,
    CFI_INTERVIEW_TICK,
    CFI_LAUNCH_RESULT,
    CFI_OPEN,
    CFI_REPORT_WRITE,
    CFI_SAVE,
    CFI_START_TICK,
    CFI_TREND_QUERY,
    CHAT_READ_REPLIES,
    CHAT_SEND_QUESTION,
    COA_FETCH,
    CONTROLLED_GUIDANCE_QUERY,
    DRIVE_FETCH_EVIDENCE,
    EM_QUERY,
    ERPNEXT_NONCONFORMANCE_WRITE,
    ERPNEXT_QUALITY_ACTION_WRITE,
    LIMS_QUERY,
    MAINTENANCE_QUERY,
    OPENMAINT_CORRECTIVE_REQUEST,
    PRIOR_ART_QUERY,
    PRIOR_ART_REDACT,
    PRIOR_ART_WRITE,
    QMS_REPORT_WRITE,
    SIBLING_BATCH_QUERY,
    SPONSOR_BATCH_STATUS,
    SPONSOR_NOTIFICATION_REQUEST,
    STERILITY_BIND_RESUME_HANDLE,
    STERILITY_GET,
    STERILITY_START,
    CapaTenantMismatch,
    CaseMemoryTenantMismatch,
    CaseStatusTenantMismatch,
    CfiCalendarError,
    CfiInterviewScopeError,
    CfiInterviewTenantMismatch,
    CfiReportWriteError,
    CfiTenantMismatch,
    ControlledGuidanceTenantMismatch,
    DepartmentRecordMissing,
    DriveFetchError,
    ErpNextCapaWriteError,
    QmsReportWriteError,
    SiblingBatchTenantMismatch,
    SterilityTenantMismatch,
)
from app.domain.department_tools import (
    case_status_upsert as run_case_status_upsert,
)
from app.domain.department_tools import (
    cfi_calendar_site_review as run_cfi_calendar_site_review,
)
from app.domain.department_tools import (
    cfi_get as run_cfi_get,
)
from app.domain.department_tools import (
    cfi_interview_tick as run_cfi_interview_tick,
)
from app.domain.department_tools import (
    cfi_launch_result as run_cfi_launch_result,
)
from app.domain.department_tools import (
    cfi_open as run_cfi_open,
)
from app.domain.department_tools import (
    cfi_report_write as run_cfi_report_write,
)
from app.domain.department_tools import (
    cfi_save as run_cfi_save,
)
from app.domain.department_tools import (
    cfi_start_tick as run_cfi_start_tick,
)
from app.domain.department_tools import (
    cfi_trend_query as run_cfi_trend_query,
)
from app.domain.department_tools import (
    chat_read_replies as run_chat_read_replies,
)
from app.domain.department_tools import (
    chat_send_question as run_chat_send_question,
)
from app.domain.department_tools import (
    coa_fetch as load_coa,
)
from app.domain.department_tools import (
    controlled_guidance_query as run_controlled_guidance_query,
)
from app.domain.department_tools import (
    drive_fetch_evidence as run_drive_fetch_evidence,
)
from app.domain.department_tools import (
    em_query as load_em,
)
from app.domain.department_tools import (
    erpnext_nonconformance_write as run_erpnext_nonconformance_write,
)
from app.domain.department_tools import (
    erpnext_quality_action_write as run_erpnext_quality_action_write,
)
from app.domain.department_tools import (
    lims_query as load_lims,
)
from app.domain.department_tools import (
    maintenance_query as load_maintenance,
)
from app.domain.department_tools import (
    openmaint_corrective_request as run_openmaint_corrective_request,
)
from app.domain.department_tools import (
    prior_art_query as run_prior_art_query,
)
from app.domain.department_tools import (
    prior_art_redact as run_prior_art_redact,
)
from app.domain.department_tools import (
    prior_art_write as run_prior_art_write,
)
from app.domain.department_tools import (
    qms_report_write as run_qms_report_write,
)
from app.domain.department_tools import (
    sibling_batch_query as run_sibling_batch_query,
)
from app.domain.department_tools import (
    sponsor_batch_status as run_sponsor_batch_status,
)
from app.domain.department_tools import (
    sterility_bind_resume_handle as run_sterility_bind_resume_handle,
)
from app.domain.department_tools import (
    sterility_get as run_sterility_get,
)
from app.domain.department_tools import (
    sterility_start as run_sterility_start,
)
from app.domain.guidance_models import GuidanceQueryResult
from app.domain.post_qp_models import (
    CorrectiveMaintenanceRequest,
    CorrectiveMaintenanceResult,
    SponsorNotificationRequest,
    SponsorNotificationResult,
)
from app.domain.qms_report_models import QmsReportWriteResult
from app.domain.sponsor_models import SponsorBatchStatusResult, SponsorScopeDenied
from app.domain.sterility_models import SterilityIncubationRecord

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    openWorldHint=False,
    destructiveHint=False,
)

# start/bind mutate the sterility clock. Not read-only, but idempotent
# (start returns the existing row; bind overwrites the same key) and never
# destructive — no row is ever deleted through this surface.
_IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False,
    idempotentHint=True,
    openWorldHint=False,
    destructiveHint=False,
)
_CAS_WRITE = ToolAnnotations(
    readOnlyHint=False,
    idempotentHint=False,
    openWorldHint=False,
    destructiveHint=False,
)

CFI_TICK_CALLER_SERVICE_ACCOUNTS_ENV = "COUNTERSIGN_CFI_TICK_CALLER_SERVICE_ACCOUNTS"
SPONSOR_LIAISON_URL_ENV = "COUNTERSIGN_SPONSOR_LIAISON_URL"
SPONSOR_NOTIFICATION_PATH = "/internal/sponsor-notifications"
_MAX_SPONSOR_RESPONSE_BYTES = 512 * 1024


class SponsorNotificationRelayError(Exception):
    """Department MCP could not deliver a governed liaison request."""


def _relay_sponsor_notification(
    request: SponsorNotificationRequest,
) -> SponsorNotificationResult:
    base_url = os.environ.get(SPONSOR_LIAISON_URL_ENV, "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise SponsorNotificationRelayError(
            f"{SPONSOR_LIAISON_URL_ENV} must be an HTTPS service URL without userinfo."
        )
    try:
        auth_request = google.auth.transport.requests.Request()
        token = id_token.fetch_id_token(auth_request, base_url)
        http_request = urllib.request.Request(
            f"{base_url}{SPONSOR_NOTIFICATION_PATH}",
            data=request.model_dump_json().encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(http_request, timeout=15) as response:
            body = response.read(_MAX_SPONSOR_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise SponsorNotificationRelayError(
            f"Sponsor liaison returned HTTP {exc.code}."
        ) from exc
    except urllib.error.URLError as exc:
        raise SponsorNotificationRelayError(
            "Sponsor liaison transport failed."
        ) from exc
    except Exception as exc:
        raise SponsorNotificationRelayError(
            "Could not mint the sponsor liaison identity token."
        ) from exc
    if len(body) > _MAX_SPONSOR_RESPONSE_BYTES:
        raise SponsorNotificationRelayError("Sponsor liaison response exceeded the size limit.")
    try:
        result = SponsorNotificationResult.model_validate_json(body)
    except Exception as exc:
        raise SponsorNotificationRelayError(
            "Sponsor liaison returned an invalid response."
        ) from exc
    if result.idempotency_key != request.idempotency_key:
        raise SponsorNotificationRelayError(
            "Sponsor liaison returned a mismatched idempotency key."
        )
    return result


def _cfi_tick_allowed_callers() -> set[str]:
    raw = os.environ.get(CFI_TICK_CALLER_SERVICE_ACCOUNTS_ENV, "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _mcp_http_request(mcp: FastMCP) -> Any | None:
    """The Starlette request for the in-flight MCP call, if any.

    ``FastMCP.get_context()`` raises when no request is in flight (unit
    tests calling the Python function directly, in-process transport), so
    this returns ``None`` rather than propagating that as an unrelated error.
    """
    try:
        request_context = mcp.get_context().request_context
    except Exception:
        return None
    return getattr(request_context, "request", None)


def _require_cfi_tick_caller(mcp: FastMCP) -> None:
    """Scope ``cfi_interview_tick`` to the scheduler's own invoker identity.

    Agent Gateway tool-name ALLOW policies (``mcp-tool-policy-bindings.yaml``)
    only govern Agent Identity traffic egressing through the Gateway. The
    Cloud Scheduler tick service calls this Cloud Run service directly, as a
    normal OIDC-authenticated caller — Gateway never sees that call. Without
    an explicit identity check here, holding ``roles/run.invoker`` on this
    service (which the CFI Runtime's own MCP invoker service account also
    holds, for ``chat_send_question``/``chat_read_replies``) would be enough
    to call the scheduler-only control-plane tick as well.

    Fails closed: unset config, a missing/invalid bearer token, or a caller
    outside the explicit allow-list all raise, never silently pass through.
    """
    allowed = _cfi_tick_allowed_callers()
    if not allowed:
        raise ToolError(
            f"{CFI_TICK_CALLER_SERVICE_ACCOUNTS_ENV} is not configured; "
            "refusing to run the CFI interview tick for an unscoped caller."
        )
    request = _mcp_http_request(mcp)
    if request is None:
        raise ToolError("cfi_interview_tick requires an HTTP caller identity.")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ToolError("cfi_interview_tick requires a bearer token.")
    # Cloud Run terminates TLS and forwards requests to the container over
    # plain HTTP, so request.url.scheme is always "http" internally.  The
    # OIDC token minted by the tick service uses the public "https://" base
    # URL (from COUNTERSIGN_MCP_URL), so reconstruct the audience from the
    # X-Forwarded-Proto header that Cloud Run sets on every external request.
    forwarded_scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    audience = f"{forwarded_scheme}://{request.url.netloc}"
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token as google_id_token

        claims = google_id_token.verify_oauth2_token(
            token, google.auth.transport.requests.Request(), audience
        )
    except Exception as exc:
        raise ToolError(f"cfi_interview_tick caller token is invalid: {exc}") from exc
    email = str(claims.get("email", "")).strip().lower()
    if email not in allowed:
        raise ToolError(f"Caller {email or 'unknown'!r} may not call cfi_interview_tick.")


def build_department_mcp(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    json_response: bool = False,
    stateless_http: bool = True,
) -> FastMCP:
    """Registers governed read and post-QP write department tools."""
    mcp = FastMCP(
        "countersign-department",
        host=host,
        port=port,
        json_response=json_response,
        stateless_http=stateless_http,
    )

    @mcp.tool(
        name=LIMS_QUERY,
        title="LIMS query",
        description=(
            "Read finished-product LIMS results for one tenant and batch. "
            "Cloud SQL connector shape. Read-only."
        ),
        annotations=_READ_ONLY,
    )
    def lims_query(
        tenant_id: str,
        batch_id: str,
        lifecycle_state: str = "classified",
        case_tenant_id: str = "",
    ) -> LimsSnapshot:
        del lifecycle_state, case_tenant_id
        try:
            return load_lims(tenant_id=tenant_id, batch_id=batch_id)
        except DepartmentRecordMissing as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=EM_QUERY,
        title="EM query",
        description=(
            "Read environmental-monitoring points for one tenant and batch. "
            "BigQuery connector shape. Read-only."
        ),
        annotations=_READ_ONLY,
    )
    def em_query(
        tenant_id: str,
        batch_id: str,
        lifecycle_state: str = "classified",
        case_tenant_id: str = "",
    ) -> EmSnapshot:
        del lifecycle_state, case_tenant_id
        try:
            return load_em(tenant_id=tenant_id, batch_id=batch_id)
        except DepartmentRecordMissing as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=COA_FETCH,
        title="CoA fetch",
        description=(
            "Read the supplier certificate of analysis for one tenant and batch. "
            "Drive-normalized connector shape. Read-only."
        ),
        annotations=_READ_ONLY,
    )
    def coa_fetch(
        tenant_id: str,
        batch_id: str,
        lifecycle_state: str = "classified",
        case_tenant_id: str = "",
    ) -> CoaSnapshot:
        del lifecycle_state, case_tenant_id
        try:
            return load_coa(tenant_id=tenant_id, batch_id=batch_id)
        except DepartmentRecordMissing as exc:
            raise ToolError(str(exc)) from exc
        except ERPNextApiError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=MAINTENANCE_QUERY,
        title="Maintenance history query",
        description=(
            "Read openMAINT maintenance and calibration evidence for the "
            "ERPNext Work-Order-bound equipment of one tenant and batch. "
            "Read-only."
        ),
        annotations=_READ_ONLY,
    )
    def maintenance_query(
        tenant_id: str,
        batch_id: str,
        lifecycle_state: str = "classified",
        case_tenant_id: str = "",
    ) -> MaintenanceSnapshot:
        if not case_tenant_id or case_tenant_id != tenant_id:
            raise ToolError("Maintenance query tenant scope does not match the case.")
        if lifecycle_state not in {"classified", "under_investigation"}:
            raise ToolError("Maintenance query is not available in this case lifecycle state.")
        try:
            return load_maintenance(tenant_id=tenant_id, batch_id=batch_id)
        except (DepartmentRecordMissing, ERPNextApiError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=DRIVE_FETCH_EVIDENCE,
        title="Drive fetch evidence",
        description=(
            "Fetch one scanned BMR page from Google Drive by file id, "
            "base64-encoded and ready for evidence ingest. Read-only."
        ),
        annotations=_READ_ONLY,
    )
    def drive_fetch_evidence(
        tenant_id: str, file_id: str
    ) -> DriveEvidencePayload:
        try:
            return run_drive_fetch_evidence(tenant_id=tenant_id, file_id=file_id)
        except DriveFetchError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=QMS_REPORT_WRITE,
        title="File QMS investigation report",
        description=(
            "File (or re-file) the investigation report and Annex 16 "
            "confirmation package as a Google Doc in the deviation-reports "
            "folder. Idempotent by tenant_id/case_id: a rerun overwrites the "
            "existing filed doc rather than creating a duplicate. The only "
            "write tool on this MCP surface — never merged into the "
            "read-only department or Drive allow-lists."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def qms_report_write(
        tenant_id: str, case_id: str, title: str, body: str
    ) -> QmsReportWriteResult:
        try:
            return run_qms_report_write(
                tenant_id=tenant_id, case_id=case_id, title=title, body=body
            )
        except QmsReportWriteError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=ERPNEXT_NONCONFORMANCE_WRITE,
        title="Create ERPNext Non Conformance",
        description=(
            "Create (or re-use) a Non Conformance record in ERPNext for "
            "this deviation case. Idempotent by tenant_id/case_id: a rerun "
            "re-uses the existing NC rather than duplicating it. The "
            "subject and details are pre-built deterministic text; this "
            "tool never drafts NC content. Only callable when "
            "COUNTERSIGN_COA_BACKEND=erpnext. Its own allow-list, disjoint "
            "from every read-only set and from QMS_WRITE_TOOLS."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def erpnext_nonconformance_write(
        tenant_id: str,
        case_id: str,
        batch_id: str,
        subject: str,
        details: str,
        case_tenant_id: str,
    ) -> NonConformanceWriteResult:
        try:
            return run_erpnext_nonconformance_write(
                tenant_id=tenant_id,
                case_id=case_id,
                batch_id=batch_id,
                subject=subject,
                details=details,
                case_tenant_id=case_tenant_id,
            )
        except (ErpNextCapaWriteError, CapaTenantMismatch) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=ERPNEXT_QUALITY_ACTION_WRITE,
        title="Create ERPNext Quality Action (CAPA)",
        description=(
            "Create (or re-use) a Quality Action (CAPA) in ERPNext linked "
            "to a Non Conformance. Idempotent by "
            "tenant_id/case_id/action_type. The problem and resolution "
            "text are pre-built deterministic text; this tool never drafts "
            "CAPA content. Only callable when "
            "COUNTERSIGN_COA_BACKEND=erpnext."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def erpnext_quality_action_write(
        tenant_id: str,
        case_id: str,
        batch_id: str,
        non_conformance_id: str,
        action_type: str,
        problem: str,
        resolution: str,
        case_tenant_id: str,
    ) -> QualityActionWriteResult:
        try:
            return run_erpnext_quality_action_write(
                tenant_id=tenant_id,
                case_id=case_id,
                batch_id=batch_id,
                non_conformance_id=non_conformance_id,
                action_type=action_type,
                problem=problem,
                resolution=resolution,
                case_tenant_id=case_tenant_id,
            )
        except (ErpNextCapaWriteError, CapaTenantMismatch) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=OPENMAINT_CORRECTIVE_REQUEST,
        title="Request openMAINT corrective maintenance",
        description=(
            "Create or re-use a CorrectiveMaint process at its Request activity. "
            "Allowed only after QP review and idempotent by tenant/case."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def openmaint_corrective_request(
        tenant_id: str,
        case_id: str,
        batch_id: str,
        summary: str,
        idempotency_key: str,
        lifecycle_state: str,
        case_tenant_id: str,
    ) -> CorrectiveMaintenanceResult:
        try:
            return run_openmaint_corrective_request(
                request=CorrectiveMaintenanceRequest(
                    tenant_id=tenant_id,
                    case_id=case_id,
                    batch_id=batch_id,
                    summary=summary,
                    idempotency_key=idempotency_key,
                    lifecycle_state=lifecycle_state,
                ),
                case_tenant_id=case_tenant_id,
            )
        except (
            ErpNextCapaWriteError,
            CapaTenantMismatch,
            DepartmentRecordMissing,
            ERPNextApiError,
            OpenMaintApiError,
            RuntimeError,
        ) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=SPONSOR_NOTIFICATION_REQUEST,
        title="Notify sponsor QA after QP review",
        description=(
            "Relay one tenant-locked, idempotent post-QP notification through "
            "the sponsor liaison service."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def sponsor_notification_request(
        tenant_id: str,
        case_id: str,
        batch_id: str,
        qp_outcome: str,
        qp_recorded_at: str,
        idempotency_key: str,
        lifecycle_state: str,
        case_tenant_id: str,
        decision_authority: str = "none",
    ) -> SponsorNotificationResult:
        if not case_tenant_id or tenant_id != case_tenant_id:
            raise ToolError("Sponsor notification tenant scope does not match the case.")
        try:
            payload = SponsorNotificationRequest(
                tenant_id=tenant_id,
                case_id=case_id,
                batch_id=batch_id,
                qp_outcome=qp_outcome,
                qp_recorded_at=qp_recorded_at,
                idempotency_key=idempotency_key,
                lifecycle_state=lifecycle_state,
                decision_authority=decision_authority,
            )
            if payload.lifecycle_state != "qp_reviewed":
                raise SponsorNotificationRelayError(
                    "Sponsor notification is allowed only after QP review."
                )
            return _relay_sponsor_notification(payload)
        except (ValueError, SponsorNotificationRelayError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=STERILITY_START,
        title="Start sterility incubation",
        description=(
            "Start (or return the existing) sterility incubation clock for "
            "one tenant and batch. hold_seconds is required (there is no "
            "platform default) unless the deployment's demo-compression "
            "env var is set. Idempotent. This is a lab clock, not a batch "
            "disposition."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def sterility_start(
        tenant_id: str,
        batch_id: str,
        case_id: str,
        workflow_version: str,
        case_tenant_id: str,
        hold_seconds: int | None = None,
    ) -> SterilityIncubationRecord:
        try:
            return run_sterility_start(
                tenant_id=tenant_id,
                batch_id=batch_id,
                case_id=case_id,
                workflow_version=workflow_version,
                case_tenant_id=case_tenant_id,
                hold_seconds=hold_seconds,
            )
        except SterilityTenantMismatch as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=STERILITY_GET,
        title="Get sterility incubation",
        description=(
            "Read the sterility incubation clock for one tenant and batch. "
            "Returns null if none has started."
        ),
        annotations=_READ_ONLY,
    )
    def sterility_get(
        tenant_id: str, batch_id: str, case_tenant_id: str
    ) -> SterilityIncubationRecord | None:
        try:
            return run_sterility_get(
                tenant_id=tenant_id,
                batch_id=batch_id,
                case_tenant_id=case_tenant_id,
            )
        except SterilityTenantMismatch as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=STERILITY_BIND_RESUME_HANDLE,
        title="Bind sterility resume handle",
        description=(
            "Record where the paused case invocation lives, so a scheduler "
            "tick weeks later can resume it. Overwrites any prior handle for "
            "the same tenant and batch."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def sterility_bind_resume_handle(
        tenant_id: str,
        batch_id: str,
        app_name: str,
        user_id: str,
        session_id: str,
        invocation_id: str,
        interrupt_id: str,
        case_tenant_id: str,
    ) -> SterilityIncubationRecord:
        try:
            return run_sterility_bind_resume_handle(
                tenant_id=tenant_id,
                batch_id=batch_id,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                invocation_id=invocation_id,
                interrupt_id=interrupt_id,
                case_tenant_id=case_tenant_id,
            )
        except (KeyError, SterilityTenantMismatch) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CASE_STATUS_UPSERT,
        title="Case status upsert",
        description=(
            "Upsert one batch's status row (on_sterility_hold / qp_confirmed "
            "/ qp_declined / qp_could_not_confirm). Called by the case "
            "workflow only; not a batch release."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def case_status_upsert(
        tenant_id: str,
        batch_id: str,
        case_id: str,
        status: str,
        updated_at: str,
        case_tenant_id: str,
        sterility_due_at: str | None = None,
        qp_confirmed_at: str | None = None,
    ) -> CaseStatusRecord:
        try:
            return run_case_status_upsert(
                tenant_id=tenant_id,
                batch_id=batch_id,
                case_id=case_id,
                status=status,
                updated_at=updated_at,
                case_tenant_id=case_tenant_id,
                sterility_due_at=sterility_due_at,
                qp_confirmed_at=qp_confirmed_at,
            )
        except CaseStatusTenantMismatch as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=SPONSOR_BATCH_STATUS,
        title="Sponsor batch status",
        description=(
            "The one cross-company read a sponsor's agent may perform: "
            "coarse lifecycle status for one batch, if and only if that "
            "sponsor's Quality Agreement covers this tenant. Read-only. Not "
            "a batch release, and never returns investigation findings, "
            "citations, or internal case identifiers."
        ),
        annotations=_READ_ONLY,
    )
    def sponsor_batch_status(
        sponsor_id: str, tenant_id: str, batch_id: str
    ) -> SponsorBatchStatusResult:
        try:
            return run_sponsor_batch_status(
                sponsor_id=sponsor_id, tenant_id=tenant_id, batch_id=batch_id
            )
        except SponsorScopeDenied as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=PRIOR_ART_QUERY,
        title="Prior-art query",
        description=(
            "Tenant-filtered semantic retrieval of prior-deviation facts "
            "from Case Memory. Advisory input for Investigation, never a "
            "conclusion. Read-only."
        ),
        annotations=_READ_ONLY,
    )
    async def prior_art_query(
        tenant_id: str, query: str, case_tenant_id: str, top_k: int = 5
    ) -> CaseMemoryQueryResult:
        try:
            return await run_prior_art_query(
                tenant_id=tenant_id,
                query=query,
                case_tenant_id=case_tenant_id,
                top_k=top_k,
            )
        except CaseMemoryTenantMismatch as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=PRIOR_ART_WRITE,
        title="Prior-art write",
        description=(
            "Write one fact about a case into Case Memory, with its source "
            "and confidence, for future prior-art retrieval."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    async def prior_art_write(
        tenant_id: str,
        case_id: str,
        batch_id: str,
        fact: str,
        source_uri: str,
        confidence: float,
        case_tenant_id: str,
        fact_id: str | None = None,
        root_cause_category: str | None = None,
    ) -> CaseMemoryFact:
        try:
            return await run_prior_art_write(
                tenant_id=tenant_id,
                case_id=case_id,
                batch_id=batch_id,
                fact=fact,
                source_uri=source_uri,
                confidence=confidence,
                case_tenant_id=case_tenant_id,
                fact_id=fact_id,
                root_cause_category=root_cause_category,
            )
        except CaseMemoryTenantMismatch as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=PRIOR_ART_REDACT,
        title="Prior-art redact",
        description=(
            "Redact one poisoned or incorrect Case Memory fact by id. "
            "Tenant-scoped; does not delete other facts."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    async def prior_art_redact(
        tenant_id: str, fact_id: str, reason: str, case_tenant_id: str
    ) -> CaseMemoryFact:
        try:
            return await run_prior_art_redact(
                tenant_id=tenant_id,
                fact_id=fact_id,
                reason=reason,
                case_tenant_id=case_tenant_id,
            )
        except (KeyError, CaseMemoryTenantMismatch) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CONTROLLED_GUIDANCE_QUERY,
        title="Controlled-guidance query",
        description=(
            "Tenant-scoped, manifest-verified retrieval of controlled SOP "
            "or public regulatory-guidance citations via Vertex AI RAG "
            "Engine. corpus must be 'controlled_sop' or 'public_guidance'. "
            "Advisory input for Investigation, never a conclusion. "
            "Read-only."
        ),
        annotations=_READ_ONLY,
    )
    async def controlled_guidance_query(
        tenant_id: str, corpus: str, query: str, case_tenant_id: str, top_k: int = 5
    ) -> GuidanceQueryResult:
        try:
            return await run_controlled_guidance_query(
                tenant_id=tenant_id,
                corpus=corpus,
                query=query,
                case_tenant_id=case_tenant_id,
                top_k=top_k,
            )
        except ControlledGuidanceTenantMismatch as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=SIBLING_BATCH_QUERY,
        title="Sibling batch query",
        description=(
            "Read-only, tenant-locked search for other batches on this "
            "tenant sharing a suspect attribute (currently material_lot) "
            "with the named batch. Returns associations only, never "
            "causation. Scope expansion may cross batches within one "
            "tenant; never across tenants."
        ),
        annotations=_READ_ONLY,
    )
    def sibling_batch_query(
        tenant_id: str,
        batch_id: str,
        shared_attribute: str,
        case_tenant_id: str,
    ) -> SiblingBatchQueryResult:
        try:
            return run_sibling_batch_query(
                tenant_id=tenant_id,
                batch_id=batch_id,
                shared_attribute=shared_attribute,
                case_tenant_id=case_tenant_id,
            )
        except SiblingBatchTenantMismatch as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CFI_OPEN,
        title="Open CFI",
        description=(
            "Atomically open or return the existing Cross-Functional "
            "Investigation for one tenant and source case. Idempotent by "
            "tenant_id/source_case_id; never a batch disposition or QP record."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def cfi_open(
        tenant_id: str, case_tenant_id: str, investigation: dict
    ) -> CfiInvestigation:
        try:
            return run_cfi_open(
                tenant_id=tenant_id,
                case_tenant_id=case_tenant_id,
                investigation=investigation,
            )
        except (
            CfiConcurrencyError,
            CfiTenantMismatch,
            ValueError,
        ) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CFI_GET,
        title="CFI get",
        description=(
            "Read one tenant-bound Cross-Functional Investigation record by "
            "id. Returns null if none exists. Read-only."
        ),
        annotations=_READ_ONLY,
    )
    def cfi_get(
        tenant_id: str, case_tenant_id: str, cfi_id: str
    ) -> CfiInvestigation | None:
        try:
            return run_cfi_get(
                tenant_id=tenant_id,
                case_tenant_id=case_tenant_id,
                cfi_id=cfi_id,
            )
        except CfiTenantMismatch as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CFI_SAVE,
        title="CFI compare-and-save",
        description=(
            "Persist one tenant-bound Cross-Functional Investigation "
            "aggregate with optimistic revision checking. A stale revision "
            "is rejected; never a batch disposition or QP record."
        ),
        annotations=_CAS_WRITE,
    )
    def cfi_save(
        tenant_id: str, case_tenant_id: str, investigation: dict
    ) -> CfiInvestigation:
        try:
            return run_cfi_save(
                tenant_id=tenant_id,
                case_tenant_id=case_tenant_id,
                investigation=investigation,
            )
        except (
            CfiConcurrencyError,
            CfiNotFoundError,
            CfiTenantMismatch,
            ValueError,
        ) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CFI_TREND_QUERY,
        title="CFI trend correlation query",
        description=(
            "Read-only, tenant-locked historical trend comparison for one "
            "CFI: environmental-monitoring parameter comparison, LIMS test "
            "comparison against a named sibling batch, or this tenant's own "
            "prior-CFI recurrence count. Returns an association only, never "
            "causation."
        ),
        annotations=_READ_ONLY,
    )
    def cfi_trend_query(
        tenant_id: str,
        batch_id: str,
        cfi_id: str,
        case_tenant_id: str,
        kind: str,
        compare_batch_id: str | None = None,
        parameter: str | None = None,
        test_code: str | None = None,
    ) -> CfiTrendResult:
        try:
            return run_cfi_trend_query(
                tenant_id=tenant_id,
                batch_id=batch_id,
                cfi_id=cfi_id,
                case_tenant_id=case_tenant_id,
                kind=kind,
                compare_batch_id=compare_batch_id,
                parameter=parameter,
                test_code=test_code,
            )
        except (CfiTenantMismatch, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CFI_CALENDAR_SITE_REVIEW,
        title="Schedule CFI site review",
        description=(
            "Create or return the tenant-configured Google Calendar site-"
            "review event for a durably closed CFI. The server derives the "
            "calendar, attendees, delay, and duration; the caller cannot "
            "choose those values. Idempotent by CFI id and never a batch "
            "disposition or release decision."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def cfi_calendar_site_review(
        tenant_id: str,
        case_tenant_id: str,
        cfi_id: str,
        source_case_id: str,
        batch_id: str,
        outcome: str,
        closed_at: str,
    ) -> CfiSiteReviewReference:
        try:
            return run_cfi_calendar_site_review(
                tenant_id=tenant_id,
                case_tenant_id=case_tenant_id,
                cfi_id=cfi_id,
                source_case_id=source_case_id,
                batch_id=batch_id,
                outcome=outcome,
                closed_at=closed_at,
            )
        except (CfiCalendarError, CfiTenantMismatch, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CFI_REPORT_WRITE,
        title="File CFI report",
        description=(
            "Create or update the deterministic Google Doc report for a "
            "durably closed CFI. The department server validates the title "
            "and body against the stored aggregate; model-authored report "
            "content is rejected. Idempotent by tenant and CFI id, and the "
            "artifact has no decision or release authority."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def cfi_report_write(
        tenant_id: str,
        case_tenant_id: str,
        cfi_id: str,
        title: str,
        body: str,
    ) -> CfiReportWriteResult:
        try:
            return run_cfi_report_write(
                tenant_id=tenant_id,
                case_tenant_id=case_tenant_id,
                cfi_id=cfi_id,
                title=title,
                body=body,
            )
        except (CfiReportWriteError, CfiTenantMismatch, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CFI_START_TICK,
        title="Claim pending CFI Runtime starts",
        description=(
            "Scheduler-only control-plane operation. Claim a bounded batch "
            "of pending or retryable CFI Runtime handoffs using durable "
            "leases. Returns CFI pointers only; the caller must start the "
            "separate CFI Runtime and record the result."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def cfi_start_tick(
        now: str,
        lease_seconds: int,
        max_starts: int,
        max_attempts: int,
    ) -> CfiLaunchTickResult:
        _require_cfi_tick_caller(mcp)
        try:
            return run_cfi_start_tick(
                now=now,
                lease_seconds=lease_seconds,
                max_starts=max_starts,
                max_attempts=max_attempts,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CFI_LAUNCH_RESULT,
        title="Record CFI Runtime launch result",
        description=(
            "Scheduler-only control-plane operation. Persist a started or "
            "failed result for one previously claimed CFI Runtime handoff. "
            "Started results are idempotently bound to one invocation id; "
            "failed results remain visible and retryable up to the configured "
            "attempt cap."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def cfi_launch_result(
        tenant_id: str,
        case_tenant_id: str,
        cfi_id: str,
        launch_status: str,
        runtime_invocation_id: str | None = None,
        launch_error: str | None = None,
    ) -> CfiLaunchResult:
        _require_cfi_tick_caller(mcp)
        try:
            return run_cfi_launch_result(
                tenant_id=tenant_id,
                case_tenant_id=case_tenant_id,
                cfi_id=cfi_id,
                launch_status=launch_status,
                runtime_invocation_id=runtime_invocation_id,
                launch_error=launch_error,
            )
        except (CfiConcurrencyError, CfiNotFoundError, CfiTenantMismatch, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CHAT_SEND_QUESTION,
        title="Send CFI interview question",
        description=(
            "Send one tenant-bound, idempotent CFI interview question or "
            "bounded nudge to the server-configured Google Chat thread. "
            "The caller cannot choose the Chat space or responder list."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def chat_send_question(
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
        try:
            return run_chat_send_question(
                tenant_id=tenant_id,
                case_tenant_id=case_tenant_id,
                interview_id=interview_id,
                cfi_id=cfi_id,
                hypothesis_id=hypothesis_id,
                round_number=round_number,
                question_number=question_number,
                question_text=question_text,
                deadline_at=deadline_at,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                invocation_id=invocation_id,
                interrupt_id=interrupt_id,
                is_nudge=is_nudge,
            )
        except (
            CfiChatError,
            CfiInterviewConcurrencyError,
            CfiInterviewScopeError,
            CfiInterviewTenantMismatch,
            ValueError,
        ) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CHAT_READ_REPLIES,
        title="Read CFI interview replies",
        description=(
            "Read new messages only from the stored Google Chat interview "
            "thread, classify them deterministically as untrusted evidence, "
            "and persist the cursor and lifecycle state."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def chat_read_replies(
        tenant_id: str,
        case_tenant_id: str,
        interview_id: str,
        finalize: bool = False,
    ) -> CfiInterviewRecord:
        try:
            return run_chat_read_replies(
                tenant_id=tenant_id,
                case_tenant_id=case_tenant_id,
                interview_id=interview_id,
                finalize=finalize,
            )
        except (
            CfiChatError,
            CfiInterviewConcurrencyError,
            CfiInterviewScopeError,
            CfiInterviewTenantMismatch,
            ValueError,
        ) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        name=CFI_INTERVIEW_TICK,
        title="Process CFI interview polling tick",
        description=(
            "Internal scheduler control-plane operation. Atomically claim a "
            "bounded batch of CFI interviews, read their stored Chat threads, "
            "send due nudges, and return only minimal resume metadata. Raw "
            "human reply text never leaves the department MCP."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def cfi_interview_tick(
        now: str,
        lease_seconds: int,
        max_interviews: int,
    ) -> CfiInterviewTickResult:
        _require_cfi_tick_caller(mcp)
        try:
            return run_cfi_interview_tick(
                now=now,
                lease_seconds=lease_seconds,
                max_interviews=max_interviews,
            )
        except (
            CfiChatError,
            CfiInterviewConcurrencyError,
            CfiInterviewScopeError,
            CfiInterviewTenantMismatch,
            ValueError,
        ) as exc:
            raise ToolError(str(exc)) from exc

    return mcp


_PUBLIC_GET_PATHS = frozenset({"/", "/health"})
_ALLOW_UNAUTH_ENV = "MCP_ALLOW_UNAUTHENTICATED"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def mcp_request_requires_bearer(*, method: str, path: str) -> bool:
    """GET / and /health are probes. MCP methods require a bearer token."""
    normalized = path.rstrip("/") or "/"
    if method.upper() == "GET" and normalized in _PUBLIC_GET_PATHS:
        return False
    if _env_flag(_ALLOW_UNAUTH_ENV):
        return False
    return True


def has_bearer_authorization(authorization: str | None) -> bool:
    if not authorization:
        return False
    scheme, _, token = authorization.partition(" ")
    return scheme.lower() == "bearer" and bool(token.strip())


class RequireBearerMiddleware:
    """Reject MCP traffic without Authorization: Bearer (IAP / identity token)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if not mcp_request_requires_bearer(method=method, path=path):
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        if has_bearer_authorization(headers.get("authorization")):
            await self.app(scope, receive, send)
            return
        body = b'{"error":"unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def department_asgi_app(mcp: FastMCP | None = None) -> Any:
    """Streamable HTTP app with health routes and bearer gate."""
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    server = mcp or build_department_mcp(
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        json_response=False,
        stateless_http=True,
    )
    app = server.streamable_http_app()

    async def health(_request: Any) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": "countersign-department"})

    app.router.routes.insert(0, Route("/", health, methods=["GET"]))
    app.router.routes.insert(0, Route("/health", health, methods=["GET"]))
    app.add_middleware(RequireBearerMiddleware)
    return app


@functools.lru_cache(maxsize=1)
def get_department_mcp() -> FastMCP:
    return build_department_mcp()


def main() -> None:
    import uvicorn

    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(department_asgi_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
