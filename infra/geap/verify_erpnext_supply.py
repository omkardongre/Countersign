#!/usr/bin/env python3
"""Live check: department MCP coa_fetch / sibling_batch_query against real ERPNext.

Confirms item 6 (ERPNext supply/CoA backend) end to end through the deployed
Cloud Run department MCP, not just unit-tested.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.domain.department_tools import COA_FETCH, SIBLING_BATCH_QUERY

TENANT = "ACME01"
BATCH = "BATCH-2026-001"


def _identity_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "print-identity-token"],
        check=True,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("gcloud auth print-identity-token returned empty.")
    return token


async def _run(service_url: str) -> None:
    mcp_url = service_url.rstrip("/") + "/mcp"
    token = _identity_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(headers=headers, timeout=60.0) as http:
        async with streamable_http_client(mcp_url, http_client=http) as (
            read,
            write,
            _session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                missing = {COA_FETCH, SIBLING_BATCH_QUERY} - tools
                if missing:
                    raise AssertionError(f"MCP tool list missing {sorted(missing)}")
                print(f"tool inventory ok: {COA_FETCH}, {SIBLING_BATCH_QUERY} present")

                coa = await session.call_tool(
                    COA_FETCH, {"tenant_id": TENANT, "batch_id": BATCH}
                )
                if coa.isError:
                    raise AssertionError(f"coa_fetch failed: {coa.content}")
                payload = coa.structuredContent or {}
                print("coa_fetch result:", payload)
                if payload.get("system") != "erpnext":
                    raise AssertionError(
                        f"coa_fetch did not resolve via ERPNext backend: {payload!r}"
                    )
                if not str(payload.get("source_uri", "")).startswith(
                    "https://34.122.229.102.nip.io/app/countersign-coa-verification/"
                ):
                    raise AssertionError(
                        f"coa_fetch source_uri is not a real ERPNext record: {payload!r}"
                    )
                print("CONFIRMED: coa_fetch reads the real ERPNext CoA Verification record.")

                siblings = await session.call_tool(
                    SIBLING_BATCH_QUERY,
                    {
                        "tenant_id": TENANT,
                        "batch_id": BATCH,
                        "shared_attribute": "material_lot",
                        "case_tenant_id": TENANT,
                    },
                )
                if siblings.isError:
                    raise AssertionError(
                        f"sibling_batch_query failed: {siblings.content}"
                    )
                sibling_payload = siblings.structuredContent or {}
                print("sibling_batch_query result:", sibling_payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default=os.environ.get("COUNTERSIGN_MCP_SERVICE_URL", ""),
        help="Cloud Run service URL (https://….run.app), no /mcp suffix",
    )
    args = parser.parse_args()
    if not args.url:
        print("Pass --url or COUNTERSIGN_MCP_SERVICE_URL", file=sys.stderr)
        return 2
    try:
        asyncio.run(_run(args.url.rstrip("/")))
    except Exception as exc:
        print(f"ERPNext supply live verify failed: {exc}", file=sys.stderr)
        return 1
    print("Department MCP ERPNext supply path ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
