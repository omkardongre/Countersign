#!/usr/bin/env python3
"""Verify Countersign's live Agent Registry catalog and fleet mapping.

This is a read-only proof command. It deliberately fails closed when a
catalog record, public card, live MCP tools/list response, or fleet manifest
changes unexpectedly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from typing import Any
from urllib.request import Request, urlopen

PROJECT = "countersign-505107"
LOCATION = "us-central1"
CASE_RUNTIME_ID = "4826409094459949056"
SPONSOR_SERVICE = "countersign-sponsor-liaison"
SPONSOR_AGENT_NAME = "sponsor_liaison_agent"
SPONSOR_SKILL = "sponsor_batch_status"
MCP_SERVICE = "countersign-department-mcp-tools"
DEFAULT_MCP_URL = "https://countersign-department-mcp-sm343mxwxq-uc.a.run.app/mcp"
EXPECTED_FLEET_SIZE = 9


def _gcloud(*args: str) -> Any:
    command = [
        "gcloud",
        "alpha",
        "agent-registry",
        *args,
        f"--project={PROJECT}",
        f"--location={LOCATION}",
        "--format=json",
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(f"gcloud Registry read failed: {detail.strip()}") from exc


def _one(value: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(value, Mapping) and key in value:
            value = value[key]
        else:
            return None
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _interface_url(value: Any) -> str | None:
    interfaces = _one(value, "supportedInterfaces") or _one(value, "interfaces") or []
    return interfaces[0].get("url") if interfaces and isinstance(interfaces[0], Mapping) else None


def _fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    _require(isinstance(payload, dict), f"Agent card at {url} was not an object")
    return payload


async def _live_mcp_tool_names(mcp_url: str) -> set[str]:
    os.environ["COUNTERSIGN_MCP_URL"] = mcp_url
    from app.mcp.client import _department_session

    async with _department_session() as session:
        result = await session.list_tools()
    return {tool.name for tool in result.tools}


def _fleet_names() -> set[str]:
    from app.domain.fleet_manifest import build_fleet_manifest

    manifest = build_fleet_manifest()
    _require(len(manifest) == EXPECTED_FLEET_SIZE, f"fleet manifest must contain {EXPECTED_FLEET_SIZE} entries")
    names = {entry.name for entry in manifest}
    _require(SPONSOR_AGENT_NAME in names, "fleet manifest is missing sponsor_liaison_agent")
    return names


def verify(mcp_url: str) -> dict[str, Any]:
    agents = _gcloud("agents", "list")
    _require(isinstance(agents, list), "Agent Registry agents list was not an array")

    case_agents = [
        agent
        for agent in agents
        if CASE_RUNTIME_ID in str(_one(agent, "agentId"))
        or CASE_RUNTIME_ID in str(_one(agent, "attributes", "agentregistry.googleapis.com/system/RuntimeReference", "uri"))
    ]
    _require(len(case_agents) == 1, f"expected one case Runtime Registry agent, found {len(case_agents)}")
    _require(case_agents[0].get("displayName") == "countersign-case", "case Runtime display name mismatch")

    sponsor = _gcloud("services", "describe", SPONSOR_SERVICE)
    sponsor_card = _one(sponsor, "agentSpec", "content")
    _require(_one(sponsor, "agentSpec", "type") == "A2A_AGENT_CARD", "sponsor service is not an A2A agent-card service")
    _require(_one(sponsor_card, "name") == SPONSOR_AGENT_NAME, "sponsor card name mismatch")
    sponsor_skills = {skill.get("id") for skill in (_one(sponsor_card, "skills") or [])}
    _require(SPONSOR_SKILL in sponsor_skills, "sponsor card is missing sponsor_batch_status")
    sponsor_url = _interface_url(sponsor_card)
    _require(sponsor_url is not None, "sponsor card has no supported interface URL")
    live_sponsor_card = _fetch_json(f"{sponsor_url}/.well-known/agent-card.json")
    live_card_content = live_sponsor_card.get("content", live_sponsor_card)
    _require(_one(live_card_content, "name") == SPONSOR_AGENT_NAME, "live sponsor card name mismatch")
    _require(SPONSOR_SKILL in {skill.get("id") for skill in (_one(live_card_content, "skills") or [])}, "live sponsor card skill mismatch")

    mcp = _gcloud("services", "describe", MCP_SERVICE)
    _require(_one(mcp, "mcpServerSpec", "type") == "TOOL_SPEC", "department service is not a TOOL_SPEC MCP service")
    catalog_url = _interface_url(mcp)
    _require(catalog_url == mcp_url, f"MCP interface URL mismatch: Registry={catalog_url!r}, expected={mcp_url!r}")
    catalog_tools = {tool.get("name") for tool in (_one(mcp, "mcpServerSpec", "content", "tools") or [])}
    _require(None not in catalog_tools and catalog_tools, "MCP Registry tool spec is empty or malformed")
    live_tools = asyncio.run(_live_mcp_tool_names(mcp_url))
    _require(catalog_tools == live_tools, f"MCP tool mismatch: Registry={sorted(catalog_tools)}, live={sorted(live_tools)}")

    fleet = _fleet_names()
    return {
        "status": "ok",
        "project": PROJECT,
        "location": LOCATION,
        "case_runtime": {"reasoning_engine": CASE_RUNTIME_ID, "registry_display_name": case_agents[0]["displayName"]},
        "sponsor_a2a": {"service": SPONSOR_SERVICE, "agent": SPONSOR_AGENT_NAME, "skill": SPONSOR_SKILL, "url": sponsor_url},
        "department_mcp": {"service": MCP_SERVICE, "url": mcp_url, "tool_count": len(live_tools), "tools": sorted(live_tools)},
        "fleet_manifest": {"agent_count": len(fleet), "agents": sorted(fleet)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-url", default=os.environ.get("COUNTERSIGN_MCP_URL", DEFAULT_MCP_URL))
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.mcp_url), indent=2, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
