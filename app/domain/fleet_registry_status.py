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

"""Live Agent Registry read-back layered onto the static fleet manifest.

``app/domain/fleet_manifest.py::build_fleet_manifest()`` stays exactly what
its own docstring says: a static, deterministic description of the agents
that exist in the graph. That description does not depend on any network
call succeeding, and this module does not change it.

This module answers a narrower, live question: *is the platform's own
catalog — Agent Registry — currently reachable, and does it currently know
about this deployment's registered surfaces* (the department MCP tool
server and the sponsor A2A agent card)? It reuses the same
``google.adk.integrations.agent_registry.AgentRegistry`` client the ADK
platform ships, the same client
``infra/geap/verify_agent_registry.py`` already proved works against this
project — this module does not shell out to ``gcloud`` and does not
maintain a second, hand-rolled catalog.

Failure mode is explicit, not silent: if Agent Registry, the department MCP
service, or the sponsor A2A service cannot be resolved, this module reports
``registry_reachable: false`` (or the specific per-surface error) rather
than fabricating a "registered" status or falling back to an invented
default. A live claim that quietly degrades to a guess when the network
call fails is exactly the kind of overclaim ``department_source.py``
already guards against for department data reads; the same discipline
applies here.

There is deliberately no per-agent "last invocation" timestamp in this
module. No durable per-agent invocation log exists anywhere in this
codebase today (case/CFI Cloud SQL rows are keyed by case/CFI, not by
agent name), and inventing one here — or worse, fabricating a plausible-
looking timestamp — would be exactly the "dummy/fallback" behaviour this
build avoids. Reporting live catalog reachability honestly is a real,
verifiable improvement over the static manifest; reporting an invented
per-agent activity feed would not be.
"""

from __future__ import annotations

import functools
import logging
import os
from enum import StrEnum

from pydantic import BaseModel

from app.domain.fleet_manifest import AgentKind, FleetAgentEntry, build_fleet_manifest
from app.mcp.client import MCP_URL_ENV

logger = logging.getLogger(__name__)

PROJECT_ENV = "GOOGLE_CLOUD_PROJECT"
LOCATION_ENV = "GOOGLE_CLOUD_LOCATION"
SPONSOR_A2A_CARD_URL_ENV = "COUNTERSIGN_SPONSOR_A2A_CARD_URL"


class RegistrySurfaceStatus(StrEnum):
    """Whether Agent Registry currently reports this deployment surface."""

    REGISTERED = "registered"
    NOT_FOUND = "not_found"
    UNREACHABLE = "unreachable"
    NOT_CONFIGURED = "not_configured"


class FleetAgentLiveStatus(BaseModel):
    """One manifest entry, plus what the live Registry read says about the
    deployment surface it runs on. ``manifest`` is unchanged from
    ``build_fleet_manifest()`` — this model only adds a live layer next to
    it, never replaces it.
    """

    manifest: FleetAgentEntry
    deployment_surface: str | None
    registry_status: RegistrySurfaceStatus


class FleetLiveStatusReport(BaseModel):
    registry_reachable: bool
    registry_error: str | None
    mcp_server_registry_status: RegistrySurfaceStatus
    sponsor_a2a_registry_status: RegistrySurfaceStatus
    agents: list[FleetAgentLiveStatus]
    decision_authority: str = "none"


def _department_mcp_deployment_surface() -> str | None:
    """The MCP server URL this deployment actually calls, if any.

    Mirrors ``app/mcp/client.py``'s own environment contract exactly: if
    ``COUNTERSIGN_MCP_URL`` is unset, department agents fall back to
    in-process FastMCP for local/test runs, and there is no deployed
    surface for Agent Registry to know about.
    """
    return os.environ.get(MCP_URL_ENV, "").strip() or None


def _sponsor_a2a_deployment_surface() -> str | None:
    """The sponsor A2A agent-card URL, if this deployment has recorded one.

    The sponsor liaison runs as its own separate Cloud Run service
    (``app/sponsor_liaison/``), not inside this process, so there is no
    single environment variable shared with department reads for it. A
    deployment records the card URL explicitly rather than this module
    guessing a Cloud Run hostname from the service name.
    """
    return os.environ.get(SPONSOR_A2A_CARD_URL_ENV, "").strip() or None


@functools.cache
def _registry_client():
    """Cached ``AgentRegistry`` client, built lazily so unit tests and local
    runs that never call ``build_fleet_live_status()`` do not pay for
    ``google.auth.default()`` or require ADC to be configured at all.
    """
    from google.adk.integrations.agent_registry import AgentRegistry

    project = os.environ.get(PROJECT_ENV, "").strip()
    location = os.environ.get(LOCATION_ENV, "").strip()
    if not project or not location:
        raise RuntimeError(
            f"{PROJECT_ENV} and {LOCATION_ENV} must both be set to read "
            "Agent Registry."
        )
    return AgentRegistry(project_id=project, location=location)


def reset_registry_client_cache() -> None:
    cache_clear = getattr(_registry_client, "cache_clear", None)
    if cache_clear is not None:
        cache_clear()


def _mcp_server_registry_status(mcp_url: str | None) -> RegistrySurfaceStatus:
    if mcp_url is None:
        return RegistrySurfaceStatus.NOT_CONFIGURED
    client = _registry_client()
    servers = client.list_mcp_servers()
    for server in servers.get("mcpServers", []):
        interfaces = server.get("interfaces") or []
        urls = {
            interface.get("url")
            for interface in interfaces
            if isinstance(interface, dict)
        }
        if mcp_url in urls:
            return RegistrySurfaceStatus.REGISTERED
    return RegistrySurfaceStatus.NOT_FOUND


def _sponsor_a2a_registry_status(card_url: str | None) -> RegistrySurfaceStatus:
    if card_url is None:
        return RegistrySurfaceStatus.NOT_CONFIGURED
    client = _registry_client()
    agents = client.list_agents()
    for agent in agents.get("agents", []):
        for protocol in agent.get("protocols", []):
            for interface in protocol.get("interfaces", []):
                if interface.get("url") == card_url:
                    return RegistrySurfaceStatus.REGISTERED
    return RegistrySurfaceStatus.NOT_FOUND


def build_fleet_live_status() -> FleetLiveStatusReport:
    """Layers a live Agent Registry read onto the static fleet manifest.

    Never raises: a Registry outage or missing credentials is reported as
    ``registry_reachable: false`` with the real error message, and every
    manifest entry still appears with ``UNREACHABLE`` rather than being
    dropped — the desk should show the fleet is real and the catalog check
    itself failed, not an empty list that looks like no fleet exists.
    """
    manifest = build_fleet_manifest()
    mcp_surface = _department_mcp_deployment_surface()
    sponsor_surface = _sponsor_a2a_deployment_surface()

    registry_error: str | None = None
    try:
        mcp_status = _mcp_server_registry_status(mcp_surface)
        sponsor_status = _sponsor_a2a_registry_status(sponsor_surface)
        registry_reachable = True
    except Exception as exc:  # Registry outage, missing ADC, network failure.
        logger.warning("Agent Registry read failed, reporting unreachable: %s", exc)
        registry_reachable = False
        registry_error = str(exc)
        mcp_status = (
            RegistrySurfaceStatus.UNREACHABLE
            if mcp_surface is not None
            else RegistrySurfaceStatus.NOT_CONFIGURED
        )
        sponsor_status = (
            RegistrySurfaceStatus.UNREACHABLE
            if sponsor_surface is not None
            else RegistrySurfaceStatus.NOT_CONFIGURED
        )

    agents: list[FleetAgentLiveStatus] = []
    for entry in manifest:
        if entry.kind is AgentKind.LLM_AGENT_CHAT_A2A_PEER:
            surface = sponsor_surface
            status = sponsor_status
        else:
            # Every other agent/workflow node reaches its tools (if any)
            # through the department MCP server; the orchestrator workflow
            # node itself has no tools but is reported against the same
            # surface since it is the graph that dispatches to those tools.
            surface = mcp_surface
            status = mcp_status
        agents.append(
            FleetAgentLiveStatus(
                manifest=entry,
                deployment_surface=surface,
                registry_status=status,
            )
        )

    return FleetLiveStatusReport(
        registry_reachable=registry_reachable,
        registry_error=registry_error,
        mcp_server_registry_status=mcp_status,
        sponsor_a2a_registry_status=sponsor_status,
        agents=agents,
        decision_authority="none",
    )
