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
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.domain.case_memory_models import (
    CaseMemoryFact,
    CaseMemoryQueryResult,
)
from app.domain.department_models import CoaSnapshot, EmSnapshot, LimsSnapshot
from app.domain.department_tools import (
    COA_FETCH,
    EM_QUERY,
    LIMS_QUERY,
    PRIOR_ART_QUERY,
    PRIOR_ART_REDACT,
    PRIOR_ART_WRITE,
    STERILITY_BIND_RESUME_HANDLE,
    STERILITY_GET,
    STERILITY_START,
    CaseMemoryTenantMismatch,
    DepartmentRecordMissing,
    SterilityTenantMismatch,
)
from app.domain.department_tools import (
    coa_fetch as load_coa,
)
from app.domain.department_tools import (
    em_query as load_em,
)
from app.domain.department_tools import (
    lims_query as load_lims,
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
    sterility_bind_resume_handle as run_sterility_bind_resume_handle,
)
from app.domain.department_tools import (
    sterility_get as run_sterility_get,
)
from app.domain.department_tools import (
    sterility_start as run_sterility_start,
)
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


def build_department_mcp(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    json_response: bool = False,
    stateless_http: bool = True,
) -> FastMCP:
    """Registers exactly ``lims_query``, ``em_query``, and ``coa_fetch``."""
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

    @mcp.tool(
        name=STERILITY_START,
        title="Start sterility incubation",
        description=(
            "Start (or return the existing) 14-day sterility incubation clock "
            "for one tenant and batch. Idempotent. This is a lab clock, not a "
            "batch disposition."
        ),
        annotations=_IDEMPOTENT_WRITE,
    )
    def sterility_start(
        tenant_id: str,
        batch_id: str,
        case_id: str,
        workflow_version: str,
        case_tenant_id: str,
    ) -> SterilityIncubationRecord:
        try:
            return run_sterility_start(
                tenant_id=tenant_id,
                batch_id=batch_id,
                case_id=case_id,
                workflow_version=workflow_version,
                case_tenant_id=case_tenant_id,
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
