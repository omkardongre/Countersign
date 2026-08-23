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

"""Sponsor liaison FastAPI service: agent 8 of 8, its own process.

Per ``final_reseach/10_gap_analysis_and_win_plan.md`` section 4.1, agent 8
is "A2A peer, **separate service**" — this module, not another node inside
``app/agent.py``'s ``Workflow``. It has its own agent card, its own
authentication (``app/sponsor_liaison/auth.py``), and its own Gateway
identity (``sponsor_liaison`` — ``infra/geap/mcp-sponsor-liaison-status-only.yaml``),
none of which the case workflow's Runtime shares.

Card and JSON-RPC routing follow the same shape ``app/app_utils/a2a.py``
already uses for the case workflow's own A2A surface
(``AgentCardBuilder`` + ``DefaultRequestHandler`` + ``add_a2a_routes_to_fastapi``),
so a judge or another engineer reading both surfaces recognizes the same
pattern rather than two different A2A integrations for no reason. The one
structural difference is intentional: the case workflow's card is built
dynamically from ``root_agent`` (a ``Workflow``); this service's card is
built by hand, because ``AgentCardBuilder`` introspects an ADK agent tree at
startup, and this service's actual agent is only constructed per-request
once the caller's sponsor identity is known
(``app/sponsor_liaison/executor.py``).
"""

from __future__ import annotations

import os

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from fastapi import FastAPI, HTTPException, Request

from app.sponsor_liaison.agent import SPONSOR_LIAISON_AGENT_NAME
from app.sponsor_liaison.auth import authenticate_sponsor_caller
from app.sponsor_liaison.executor import SponsorLiaisonAgentExecutor

RPC_PATH = "/a2a/sponsor-liaison"


def _resolve_app_url() -> str:
    if url := os.getenv("APP_URL"):
        return url
    return "http://0.0.0.0:8081"


def build_agent_card(*, app_url: str) -> AgentCard:
    """Hand-built, not ``AgentCardBuilder``-derived — see module docstring.

    ``AgentSkill`` names the one capability this service actually has:
    exactly what ``final_reseach/10_gap_analysis_and_win_plan.md`` section
    4.1 specifies for agent 8, "scoped read-only batch status," and nothing
    else. A sponsor reading this card before ever sending a message already
    sees the scope this agent will enforce.
    """
    return AgentCard(
        name=SPONSOR_LIAISON_AGENT_NAME,
        description=(
            "Cross-company batch-status liaison for a contract "
            "manufacturer (CDMO). Answers a sponsor's own QA agent a "
            "single scoped question about one of their own batches. "
            "Denies every other tenant's data at the CDMO's own Quality "
            "Agreement boundary. Never certifies or releases a batch."
        ),
        version=os.getenv("AGENT_VERSION", "0.1.0"),
        default_input_modes=["text", "text/plain"],
        default_output_modes=["text", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"{app_url}{RPC_PATH}",
            )
        ],
        skills=[
            AgentSkill(
                id="sponsor_batch_status",
                name="Sponsor batch status",
                description=(
                    "Look up whether one of your own batches is on "
                    "sterility hold or has a recorded QP decision. Requires "
                    "your sponsor API key and your CDMO tenant id. Never "
                    "returns investigation findings, citations, or another "
                    "sponsor's data."
                ),
                tags=["quality", "batch-status", "cross-company"],
                examples=[
                    "What is the status of batch AX-2291 under tenant aurex?"
                ],
            )
        ],
    )


class _SponsorServerCallContextBuilder(DefaultServerCallContextBuilder):
    """Stashes the authenticated ``sponsor_id`` where
    ``SponsorLiaisonAgentExecutor`` reads it (``call_context.state``).

    Authentication runs here, in the context builder, rather than as a
    FastAPI route dependency, because ``add_a2a_routes_to_fastapi`` owns the
    actual route registration and does not expose a hook to inject a
    per-route dependency into the JSON-RPC endpoint it builds. The context
    builder is the one extension point the library gives every request,
    which is exactly why ``app/app_utils/a2a.py`` already subclasses it for
    its own concern (A2A-Version header inference). ``authenticate_sponsor_caller``
    raises ``HTTPException`` on failure, which FastAPI turns into the
    correct 401/403/500 response before ``SponsorLiaisonAgentExecutor.execute``
    is ever reached.
    """

    def build(self, request: Request):
        context = super().build(request)
        context.state["sponsor_id"] = authenticate_sponsor_caller(request)
        return context


def build_app() -> FastAPI:
    app = FastAPI(
        title="countersign-sponsor-liaison",
        description=(
            "Agent 8 of 8: cross-company batch-status liaison, a separate "
            "service from the case workflow Runtime."
        ),
    )

    agent_card = build_agent_card(app_url=_resolve_app_url())
    request_handler = DefaultRequestHandler(
        agent_executor=SponsorLiaisonAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(
            agent_card, card_url=f"{RPC_PATH}/.well-known/agent-card.json"
        ),
        jsonrpc_routes=create_jsonrpc_routes(
            request_handler,
            rpc_url=RPC_PATH,
            context_builder=_SponsorServerCallContextBuilder(),
        ),
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "server": "countersign-sponsor-liaison"}

    return app


app = build_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("SPONSOR_LIAISON_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8081"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
