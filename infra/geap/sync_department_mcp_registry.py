#!/usr/bin/env python3
"""Synchronize the Department MCP's Agent Registry tool catalog.

Agent Registry intentionally limits a manually-uploaded MCP tool spec to
10 KB.  The live FastMCP ``tools/list`` result includes verbose Pydantic
schemas and is much larger, so this command derives a compact, valid One MCP
tool specification from the same server code that is deployed.  The Registry
catalog remains discoverable while Cloud Run remains the authoritative
validator for every actual tool call.

Run this after each Department MCP deployment.  It fails closed if the compact
spec would exceed the Registry limit or the Registry update fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from typing import Any

PROJECT = "countersign-505107"
LOCATION = "us-central1"
SERVICE = "countersign-department-mcp-tools"
MAX_SPEC_BYTES = 10 * 1024


def _compact_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep required parameter names/types while fitting Registry's 10 KB cap.

    FastMCP's complete Pydantic schemas contain presentation metadata and
    optional/defaulted internal context fields.  These fields are not an
    authorization contract: each call is still validated by the live MCP
    server.  Keeping required inputs provides useful discovery without
    publishing a stale or oversize pseudo-copy of the server schema.
    """
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise RuntimeError("MCP tool input schema was malformed")

    compact_properties: dict[str, dict[str, str]] = {}
    for name in required:
        if not isinstance(name, str):
            raise RuntimeError("MCP tool required parameter was not a string")
        field = properties.get(name, {})
        field_type = field.get("type", "string") if isinstance(field, dict) else "string"
        compact_properties[name] = {
            "type": field_type if isinstance(field_type, str) else "string"
        }

    result: dict[str, Any] = {"type": "object", "properties": compact_properties}
    if required:
        result["required"] = required
    return result


async def build_tool_spec() -> dict[str, Any]:
    """Build the compact catalog from the actual local FastMCP tool registry."""
    from app.mcp.department_server import get_department_mcp

    tools = await get_department_mcp().list_tools()
    entries: list[dict[str, Any]] = []
    names: set[str] = set()
    for tool in tools:
        if not tool.name or tool.name in names:
            raise RuntimeError(f"invalid or duplicate Department MCP tool name: {tool.name!r}")
        names.add(tool.name)
        entries.append(
            {
                "name": tool.name,
                "description": tool.name.replace("_", " "),
                "inputSchema": _compact_input_schema(tool.inputSchema),
            }
        )
    if not entries:
        raise RuntimeError("Department MCP exposed no tools")
    return {"tools": sorted(entries, key=lambda entry: entry["name"])}


def _encoded_spec(spec: dict[str, Any]) -> bytes:
    encoded = json.dumps(spec, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_SPEC_BYTES:
        raise RuntimeError(
            f"compact MCP tool spec is {len(encoded)} bytes; Agent Registry allows "
            f"at most {MAX_SPEC_BYTES} bytes"
        )
    return encoded


def update_registry(spec: dict[str, Any], *, project: str, location: str, service: str) -> None:
    """Upload one compact tool spec; preserve the registered URL and identity."""
    encoded = _encoded_spec(spec)
    with tempfile.NamedTemporaryFile(suffix=".json", mode="wb") as handle:
        handle.write(encoded)
        handle.flush()
        subprocess.run(
            [
                "gcloud",
                "alpha",
                "agent-registry",
                "services",
                "update",
                service,
                f"--project={project}",
                f"--location={location}",
                "--mcp-server-spec-type=tool-spec",
                f"--mcp-server-spec-content={handle.name}",
                "--quiet",
            ],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=PROJECT)
    parser.add_argument("--location", default=LOCATION)
    parser.add_argument("--service", default=SERVICE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        spec = asyncio.run(build_tool_spec())
        encoded = _encoded_spec(spec)
        if args.dry_run:
            print(json.dumps({"tool_count": len(spec["tools"]), "spec_bytes": len(encoded)}))
            return 0
        update_registry(spec, project=args.project, location=args.location, service=args.service)
        print(json.dumps({"status": "updated", "tool_count": len(spec["tools"]), "spec_bytes": len(encoded)}))
        return 0
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"Department MCP Registry sync failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
