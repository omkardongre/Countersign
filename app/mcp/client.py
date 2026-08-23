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

"""MCP client used by QC and supply collectors.

Runtime must set COUNTERSIGN_MCP_URL (https Streamable HTTP, typically ``…/mcp``)
so calls egress through Agent Gateway. In-process FastMCP is only used when
COUNTERSIGN_MCP_INPROCESS is explicitly set (unit tests / local graph runs).
Omitting both fails closed — collectors must not skip the gateway by default.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import google.auth
import google.auth.transport.requests
from google.auth import impersonated_credentials
from google.oauth2 import id_token
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.memory import create_connected_server_and_client_session

from app.domain.department_tools import (
    ALLOWED_DEPARTMENT_TOOLS,
    CASE_MEMORY_TOOLS,
    CASE_STATUS_TOOLS,
    DRIVE_TOOLS,
    SPONSOR_TOOLS,
    STERILITY_TOOLS,
)
from app.mcp.department_server import get_department_mcp

MCP_URL_ENV = "COUNTERSIGN_MCP_URL"
MCP_INPROCESS_ENV = "COUNTERSIGN_MCP_INPROCESS"
MCP_INVOKER_SERVICE_ACCOUNT_ENV = "COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT"
_LOCAL_HTTP_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class DepartmentToolCallError(Exception):
    """MCP tools/call failed or returned no structured payload."""


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def validate_mcp_url(url: str) -> str:
    """Require https, or http only to loopback for local tests."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return url
    if parsed.scheme == "http" and host in _LOCAL_HTTP_HOSTS:
        return url
    raise DepartmentToolCallError(
        "COUNTERSIGN_MCP_URL must be https, or http to localhost."
    )


def resolve_department_mcp_target() -> FastMCP | str:
    """Gateway URL, or in-process server if explicitly opted in."""
    url = os.environ.get(MCP_URL_ENV, "").strip()
    if url:
        return validate_mcp_url(url)
    if _env_flag(MCP_INPROCESS_ENV):
        return get_department_mcp()
    raise DepartmentToolCallError(
        "Department MCP is not configured: set COUNTERSIGN_MCP_URL "
        f"(https through Agent Gateway) or {MCP_INPROCESS_ENV}=1 for local tests."
    )


def cloud_run_audience(url: str) -> str:
    """Scheme + host of ``url``, which is the audience Cloud Run expects.

    Cloud Run validates the ``aud`` claim against the service's own base URL,
    not the request path, so ``https://host/mcp`` must be presented as
    ``https://host``.
    """
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _mcp_auth_headers(url: str) -> dict[str, str]:
    """OIDC bearer header for a private Cloud Run MCP service.

    The department MCP service is deployed ``--no-allow-unauthenticated``, so
    Cloud Run's own IAM check runs *in addition to* Agent Gateway's tool-name
    authorization. Holding ``roles/run.invoker`` is necessary but not
    sufficient: the caller must actually present a token Cloud Run accepts.

    Two things had to be found live, not by reading the API surface:

    1. Without any header at all, Cloud Run logs ``403 ... Empty
       Authorization header value`` while the Gateway log for the same
       request shows ``ALLOWED`` — two different layers, only one satisfied.
    2. Minting an ID token directly from the Agent Identity's ambient
       credentials (``id_token.fetch_id_token``) *is* sent, but Cloud Run then
       rejects it with ``401 ... The access token could not be verified``.
       Decoding that token (temporarily, via COUNTERSIGN_MCP_AUTH_DEBUG)
       showed why: its issuer is
       ``https://sts.googleapis.com/.../workloadIdentityPools/...`` and its
       subject is a ``spiffe://`` URI with no ``email`` claim — a workload
       identity federation token, not a token bound to a service account
       identity. Cloud Run's invoker check validates against a service
       account or user identity, and does not recognize this token shape,
       matching Google's own service-account-impersonation pattern for
       Workload Identity Federation callers
       (docs.cloud.google.com/iam/docs/tutorial-cloud-run-workload-id-federation).

    The fix is impersonation, not a token detail: mint the ID token as a real
    service account (``COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT``) that the
    Agent Identity is granted ``roles/iam.serviceAccountTokenCreator`` on, and
    that itself holds ``roles/run.invoker`` on the MCP service. The Agent
    Identity's own ambient credentials are only used to authenticate the
    impersonation call, never presented to Cloud Run directly.

    Cloud Run requires an ID token (OIDC), not an OAuth access token; the
    audience must be the service base URL. Loopback targets are local test
    servers with no IAM in front, so they get no header.
    """
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() in _LOCAL_HTTP_HOSTS:
        return {}

    audience = cloud_run_audience(url)
    invoker_sa = os.environ.get(MCP_INVOKER_SERVICE_ACCOUNT_ENV, "").strip()
    request = google.auth.transport.requests.Request()
    try:
        if invoker_sa:
            source_credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            # IDTokenCredentials takes an already-impersonated Credentials
            # object (target_credentials), not a bare principal string.
            impersonated_target = impersonated_credentials.Credentials(
                source_credentials=source_credentials,
                target_principal=invoker_sa,
                target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            id_creds = impersonated_credentials.IDTokenCredentials(
                impersonated_target,
                target_audience=audience,
                include_email=True,
            )
            id_creds.refresh(request)
            token = id_creds.token
        else:
            # No invoker identity configured: fall back to the ambient
            # identity's own ID token. Works for a normal service account
            # (local dev, a non-Agent-Identity Runtime); fails closed with a
            # clear Cloud Run 401 for an Agent Identity, per the finding
            # above, rather than silently sending a token that never works.
            token = id_token.fetch_id_token(request, audience)
    except Exception as exc:  # fail closed, carrying the real cause
        raise DepartmentToolCallError(
            f"Could not mint an identity token for {audience}: {exc}. The "
            "department MCP service is private, so an OIDC token is required."
        ) from exc
    if _env_flag("COUNTERSIGN_MCP_AUTH_DEBUG"):
        # Diagnostic for the live auth investigation above. Logs only token
        # shape/claims, never the token itself.
        import base64
        import json as _json

        def _pad(s: str) -> str:
            return s + "=" * (-len(s) % 4)

        try:
            header_b64, payload_b64, _sig = token.split(".")
            header = _json.loads(base64.urlsafe_b64decode(_pad(header_b64)))
            payload = _json.loads(base64.urlsafe_b64decode(_pad(payload_b64)))
            print(
                "COUNTERSIGN_MCP_AUTH_DEBUG token_header="
                f"{header} token_claims="
                f"{ {k: payload.get(k) for k in ('iss', 'aud', 'azp', 'email', 'sub', 'exp')} }"
            )
        except Exception as decode_exc:  # diagnostics only, fail closed above
            print(f"COUNTERSIGN_MCP_AUTH_DEBUG could not decode token: {decode_exc}")
    return {"Authorization": f"Bearer {token}"}


def _error_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return " ".join(parts) or "MCP tool returned isError with no text"


@asynccontextmanager
async def _department_session() -> AsyncIterator[ClientSession]:
    target = resolve_department_mcp_target()
    if isinstance(target, str):
        # streamable_http_client takes an http_client rather than headers, so
        # the OIDC header is attached to the client itself.
        # create_mcp_http_client is the library's own factory, which keeps
        # MCP's streaming timeout defaults instead of httpx's shorter ones.
        async with create_mcp_http_client(
            headers=_mcp_auth_headers(target)
        ) as http_client:
            async with streamable_http_client(target, http_client=http_client) as (
                read,
                write,
                _session_id,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        return
    async with create_connected_server_and_client_session(target) as session:
        yield session


async def call_department_tool(
    name: str,
    *,
    tenant_id: str,
    batch_id: str,
    lifecycle_state: str = "classified",
    case_tenant_id: str | None = None,
) -> dict[str, Any]:
    """Call one named department tool over MCP. Fail closed on errors."""
    if name not in ALLOWED_DEPARTMENT_TOOLS:
        raise DepartmentToolCallError(
            f"Collectors may not call {name!r}; allowed={sorted(ALLOWED_DEPARTMENT_TOOLS)}"
        )
    acting = case_tenant_id if case_tenant_id is not None else tenant_id
    payload = await call_mcp_tool(
        name,
        {
            "tenant_id": tenant_id,
            "case_tenant_id": acting,
            "lifecycle_state": lifecycle_state,
            "batch_id": batch_id,
        },
    )
    if not payload:
        raise DepartmentToolCallError(
            f"{name} returned no structured content for {tenant_id}/{batch_id}."
        )
    return payload


async def call_drive_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one named Drive tool over MCP. Fail closed on errors.

    Separate from ``call_department_tool``: ``drive_fetch_evidence`` takes a
    ``file_id`` rather than the fixed
    ``tenant_id/batch_id/lifecycle_state/case_tenant_id`` shape that helper
    hardcodes, and reaches a real Drive credential the MCP process holds —
    same reason an Agent Identity cannot open Cloud SQL or Firestore itself.
    """
    if name not in DRIVE_TOOLS:
        raise DepartmentToolCallError(
            f"Callers may not call {name!r}; allowed={sorted(DRIVE_TOOLS)}"
        )
    payload = await call_mcp_tool(name, arguments)
    if not payload:
        raise DepartmentToolCallError(f"{name} returned no structured content.")
    return payload


async def call_sterility_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one named sterility tool over MCP. Fail closed on errors.

    An Agent Identity principal cannot be a Cloud SQL IAM database user, so
    the workflow reaches the sterility clock through this MCP tool — served
    by a process holding a real service-account credential — rather than
    opening Cloud SQL directly (see ``app/domain/department_tools.py``).
    """
    if name not in STERILITY_TOOLS:
        raise DepartmentToolCallError(
            f"Callers may not call {name!r}; allowed={sorted(STERILITY_TOOLS)}"
        )
    payload = await call_mcp_tool(name, arguments)
    return payload or {}


async def call_case_memory_tool(
    name: str, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """Call one named Case Memory tool over MCP. Fail closed on errors.

    Same reason as ``call_sterility_tool``: an Agent Identity principal
    cannot itself hold Firestore IAM credentials, so Case Memory is reached
    through this MCP tool rather than opened directly.
    """
    if name not in CASE_MEMORY_TOOLS:
        raise DepartmentToolCallError(
            f"Callers may not call {name!r}; allowed={sorted(CASE_MEMORY_TOOLS)}"
        )
    return await call_mcp_tool(name, arguments)


async def call_case_status_tool(
    name: str, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """Call ``case_status_upsert`` over MCP. Case-workflow side only.

    Same reason as ``call_sterility_tool``: an Agent Identity principal
    cannot hold the Cloud SQL IAM credential this write needs.
    """
    if name not in CASE_STATUS_TOOLS:
        raise DepartmentToolCallError(
            f"Callers may not call {name!r}; allowed={sorted(CASE_STATUS_TOOLS)}"
        )
    payload = await call_mcp_tool(name, arguments)
    if not payload:
        raise DepartmentToolCallError(f"{name} returned no structured content.")
    return payload


async def call_sponsor_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call ``sponsor_batch_status`` over MCP. Sponsor-liaison side only.

    The sponsor liaison service is a separate process from the case
    workflow (per ``final_reseach/10_gap_analysis_and_win_plan.md`` section
    4.1, agent 8 is "A2A peer, separate service"), so it goes through the
    same public entry point (``resolve_department_mcp_target``) rather than
    a private import of ``sponsor_tools`` — the Gateway's own tool-name
    allow-list is what actually enforces that this service can reach
    ``sponsor_batch_status`` and nothing else on this MCP surface.
    """
    if name not in SPONSOR_TOOLS:
        raise DepartmentToolCallError(
            f"Callers may not call {name!r}; allowed={sorted(SPONSOR_TOOLS)}"
        )
    payload = await call_mcp_tool(name, arguments)
    if not payload:
        raise DepartmentToolCallError(f"{name} returned no structured content.")
    return payload


async def call_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Low-level ``tools/call``. Returns structured content, or ``None``."""
    async with _department_session() as session:
        result = await session.call_tool(name, arguments)
    if result.isError:
        raise DepartmentToolCallError(_error_text(result))
    payload = result.structuredContent
    if payload is not None and not isinstance(payload, dict):
        raise DepartmentToolCallError(
            f"{name} returned a non-object structured content payload."
        )
    return payload
