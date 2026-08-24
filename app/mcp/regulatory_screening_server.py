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

"""Dedicated read-only MCP boundary for public regulatory screening.

This service intentionally exposes only the two T1-7 tools and has no access
to Countersign tenant, case, batch, CoA, Drive, SQL, or QMS systems. The
Runtime sends public manufacturer/product/defect attributes only; the service
returns normalized, provenance-stamped advisory results.
"""

from __future__ import annotations

import functools
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.domain.regulatory_screening import (
    EUDRAGMDP_SCREEN,
    OPENFDA_ENFORCEMENT_SCREEN,
    screen_eudragmdp,
    screen_openfda_enforcement,
)
from app.domain.regulatory_screening_models import RegulatoryScreeningResult
from app.mcp.department_server import RequireBearerMiddleware

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    openWorldHint=True,
    destructiveHint=False,
)


def build_regulatory_screening_mcp(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    json_response: bool = False,
    stateless_http: bool = True,
) -> FastMCP:
    """Register exactly the two public regulatory screening tools."""
    mcp = FastMCP(
        "countersign-regulatory-screening",
        host=host,
        port=port,
        json_response=json_response,
        stateless_http=stateless_http,
    )

    @mcp.tool(
        name=EUDRAGMDP_SCREEN,
        title="EudraGMDP non-compliance screen",
        description=(
            "Search the official EudraGMDP public non-compliance portal for a "
            "public manufacturer/site identity. Results are advisory only. "
            "Do not send tenant, case, batch, internal supplier, lot, or "
            "confidential deviation identifiers."
        ),
        annotations=_READ_ONLY,
    )
    def eudragmdp_screen(
        manufacturer_name: str | None = None,
        country: str | None = None,
        city: str | None = None,
        oms_location_id: str | None = None,
    ) -> RegulatoryScreeningResult:
        try:
            return screen_eudragmdp(
                manufacturer_name=manufacturer_name,
                country=country,
                city=city,
                oms_location_id=oms_location_id,
            )
        except Exception as exc:
            # The domain client normally converts expected upstream failures
            # to a typed result. A server-side contract/programming failure is
            # still a tool error, never a fabricated no-hit.
            raise ToolError(f"EudraGMDP screening failed: {type(exc).__name__}") from exc

    @mcp.tool(
        name=OPENFDA_ENFORCEMENT_SCREEN,
        title="openFDA drug-enforcement screen",
        description=(
            "Search the official openFDA drug enforcement JSON API using "
            "public manufacturer, product, active-ingredient, or defect "
            "terms. Results are advisory only. Do not send tenant, case, "
            "batch, internal supplier, lot, or confidential deviation data."
        ),
        annotations=_READ_ONLY,
    )
    def openfda_enforcement_screen(
        manufacturer_name: str | None = None,
        product_name: str | None = None,
        active_ingredient: str | None = None,
        defect_signature: str | None = None,
    ) -> RegulatoryScreeningResult:
        try:
            return screen_openfda_enforcement(
                manufacturer_name=manufacturer_name,
                product_name=product_name,
                active_ingredient=active_ingredient,
                defect_signature=defect_signature,
            )
        except Exception as exc:
            raise ToolError(f"openFDA screening failed: {type(exc).__name__}") from exc

    return mcp


_PUBLIC_GET_PATHS = frozenset({"/", "/health"})


def regulatory_screening_asgi_app(mcp: FastMCP | None = None) -> Any:
    """Streamable HTTP app with the same bearer gate as the department MCP."""
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    server = mcp or build_regulatory_screening_mcp(
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        json_response=False,
        stateless_http=True,
    )
    app = server.streamable_http_app()

    async def health(_request: Any) -> JSONResponse:
        return JSONResponse(
            {"status": "ok", "server": "countersign-regulatory-screening"}
        )

    app.router.routes.insert(0, Route("/", health, methods=["GET"]))
    app.router.routes.insert(0, Route("/health", health, methods=["GET"]))
    app.add_middleware(RequireBearerMiddleware)
    return app


@functools.lru_cache(maxsize=1)
def get_regulatory_screening_mcp() -> FastMCP:
    return build_regulatory_screening_mcp()


def main() -> None:
    import uvicorn

    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(
        regulatory_screening_asgi_app(), host=host, port=port, log_level="info"
    )


if __name__ == "__main__":
    main()
