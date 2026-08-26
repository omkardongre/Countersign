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

"""Live fleet/Registry status layer: honest reachable/unreachable/not-found
reporting, never a fabricated "registered" default.
"""

from __future__ import annotations

import pytest

from app.domain import fleet_registry_status as frs
from app.domain.fleet_manifest import build_fleet_manifest
from app.domain.fleet_registry_status import (
    RegistrySurfaceStatus,
    build_fleet_live_status,
)
from app.mcp.client import MCP_URL_ENV


class _FakeRegistryClient:
    def __init__(self, *, mcp_urls: set[str], sponsor_urls: set[str]) -> None:
        self._mcp_urls = mcp_urls
        self._sponsor_urls = sponsor_urls

    def list_mcp_servers(self) -> dict:
        return {
            "mcpServers": [
                {"interfaces": [{"url": url}]} for url in self._mcp_urls
            ]
        }

    def list_agents(self) -> dict:
        return {
            "agents": [
                {"protocols": [{"interfaces": [{"url": url}]}]}
                for url in self._sponsor_urls
            ]
        }


class _FailingRegistryClient:
    def list_mcp_servers(self) -> dict:
        raise RuntimeError("Agent Registry unavailable (simulated outage)")

    def list_agents(self) -> dict:
        raise RuntimeError("Agent Registry unavailable (simulated outage)")


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    frs.reset_registry_client_cache()
    yield
    frs.reset_registry_client_cache()


def test_manifest_size_matches_static_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp_url = "https://countersign-department-mcp-777448403573.us-central1.run.app/mcp"
    monkeypatch.setenv(MCP_URL_ENV, mcp_url)
    monkeypatch.setattr(
        frs,
        "_registry_client",
        lambda: _FakeRegistryClient(mcp_urls={mcp_url}, sponsor_urls=set()),
    )

    report = build_fleet_live_status()

    assert len(report.agents) == len(build_fleet_manifest())
    assert report.registry_reachable is True
    assert report.registry_error is None


def test_registered_mcp_server_is_reported_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_url = "https://countersign-department-mcp-777448403573.us-central1.run.app/mcp"
    monkeypatch.setenv(MCP_URL_ENV, mcp_url)
    monkeypatch.setattr(
        frs,
        "_registry_client",
        lambda: _FakeRegistryClient(mcp_urls={mcp_url}, sponsor_urls=set()),
    )

    report = build_fleet_live_status()

    assert report.mcp_server_registry_status is RegistrySurfaceStatus.REGISTERED
    non_a2a_agents = [
        agent
        for agent in report.agents
        if agent.manifest.name != "sponsor_liaison_agent"
    ]
    assert non_a2a_agents
    assert all(
        agent.registry_status is RegistrySurfaceStatus.REGISTERED
        for agent in non_a2a_agents
    )


def test_mcp_server_not_in_catalog_is_reported_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MCP_URL_ENV, "https://example.invalid/mcp")
    monkeypatch.setattr(
        frs,
        "_registry_client",
        lambda: _FakeRegistryClient(
            mcp_urls={"https://a-different-server.invalid/mcp"}, sponsor_urls=set()
        ),
    )

    report = build_fleet_live_status()

    assert report.mcp_server_registry_status is RegistrySurfaceStatus.NOT_FOUND
    assert report.registry_reachable is True


def test_unset_mcp_url_is_reported_not_configured_without_a_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(MCP_URL_ENV, raising=False)

    def _fail_if_called() -> None:
        raise AssertionError(
            "Registry client must not be constructed when no surface is configured."
        )

    monkeypatch.setattr(frs, "_registry_client", _fail_if_called)

    report = build_fleet_live_status()

    assert report.mcp_server_registry_status is RegistrySurfaceStatus.NOT_CONFIGURED
    assert report.registry_reachable is True


def test_registry_outage_is_reported_unreachable_never_fabricated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_url = "https://countersign-department-mcp-777448403573.us-central1.run.app/mcp"
    monkeypatch.setenv(MCP_URL_ENV, mcp_url)
    monkeypatch.setattr(frs, "_registry_client", _FailingRegistryClient)

    report = build_fleet_live_status()

    assert report.registry_reachable is False
    assert report.registry_error is not None
    assert report.mcp_server_registry_status is RegistrySurfaceStatus.UNREACHABLE
    # Every manifest agent still appears — an outage must not shrink the
    # fleet view to an empty/partial list.
    assert len(report.agents) == len(build_fleet_manifest())
    non_a2a_agents = [
        agent
        for agent in report.agents
        if agent.manifest.name != "sponsor_liaison_agent"
    ]
    assert all(
        agent.registry_status is RegistrySurfaceStatus.UNREACHABLE
        for agent in non_a2a_agents
    )


def test_sponsor_a2a_peer_is_checked_against_its_own_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_url = "https://countersign-department-mcp-777448403573.us-central1.run.app/mcp"
    sponsor_url = "https://countersign-sponsor-liaison-777448403573.us-central1.run.app"
    monkeypatch.setenv(MCP_URL_ENV, mcp_url)
    monkeypatch.setenv(frs.SPONSOR_A2A_CARD_URL_ENV, sponsor_url)
    monkeypatch.setattr(
        frs,
        "_registry_client",
        lambda: _FakeRegistryClient(mcp_urls={mcp_url}, sponsor_urls={sponsor_url}),
    )

    report = build_fleet_live_status()

    assert report.sponsor_a2a_registry_status is RegistrySurfaceStatus.REGISTERED
    sponsor_agent = next(
        agent
        for agent in report.agents
        if agent.manifest.name == "sponsor_liaison_agent"
    )
    assert sponsor_agent.registry_status is RegistrySurfaceStatus.REGISTERED
    assert sponsor_agent.deployment_surface == sponsor_url


def test_fleet_status_route_never_5xxs_on_registry_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The desk must be able to render "catalog check failed" instead of
    losing the fleet view on a transient Registry outage.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.scheduler.fleet_routes import attach_fleet_routes

    monkeypatch.setenv(
        MCP_URL_ENV,
        "https://countersign-department-mcp-777448403573.us-central1.run.app/mcp",
    )
    monkeypatch.setattr(frs, "_registry_client", _FailingRegistryClient)

    app = FastAPI()
    attach_fleet_routes(app)
    client = TestClient(app)

    response = client.get("/fleet/status")

    assert response.status_code == 200
    body = response.json()
    assert body["registry_reachable"] is False
    assert body["decision_authority"] == "none"
    assert len(body["agents"]) == len(build_fleet_manifest())
