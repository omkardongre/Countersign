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

"""Streamable-HTTP MCP server for LIMS / EM / CoA reads.

One process, three exact tool names. Gateway policies (not this process) decide
which Agent Identity may call which name. stdio is not used: Agent Gateway
needs Streamable HTTP. postgres-execute-sql is not registered.

Pinned to PyPI ``mcp`` FastMCP (the API Agent Runtime already ships).
"""

from __future__ import annotations

import functools
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.domain.department_models import CoaSnapshot, EmSnapshot, LimsSnapshot
from app.domain.department_tools import (
    COA_FETCH,
    EM_QUERY,
    LIMS_QUERY,
    DepartmentRecordMissing,
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

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
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
