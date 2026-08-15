#!/usr/bin/env python3
"""Live check: authenticated Streamable HTTP to department MCP Case Memory tools.

Uses an identity token with the Cloud Run URL as audience. Cleans up probes.
Fails if query_embedded is false (embeddings/index not live on the service).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import uuid

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.domain.department_tools import PRIOR_ART_QUERY, PRIOR_ART_WRITE

PROBE_PREFIX = "mcp-case-memory-probe"


def _identity_token(audience: str) -> str:
    """User ADC cannot set ``--audiences`` (service-account only).

    Official developer invoke path is a gcloud user identity token with no
    audience claim: https://docs.cloud.google.com/run/docs/authenticating/developers
    Runtime callers still mint a service-account token with this URL as aud.
    """
    del audience  # documented: user tokens omit aud; Cloud Run still accepts them
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


def _mcp_url(service_url: str) -> str:
    return service_url.rstrip("/") + "/mcp"


async def _run(service_url: str) -> None:
    os.environ.pop("COUNTERSIGN_MCP_INPROCESS", None)
    mcp_url = _mcp_url(service_url)
    os.environ["COUNTERSIGN_MCP_URL"] = mcp_url
    token = _identity_token(service_url.rstrip("/"))
    suffix = uuid.uuid4().hex[:8]
    aurex_id = f"{PROBE_PREFIX}:aurex:{suffix}"
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
                missing = {PRIOR_ART_QUERY, PRIOR_ART_WRITE} - tools
                if missing:
                    raise AssertionError(f"MCP tool list missing {sorted(missing)}")
                print(f"tool inventory ok ({sorted(tools)})")

                written = await session.call_tool(
                    PRIOR_ART_WRITE,
                    {
                        "tenant_id": "aurex",
                        "case_tenant_id": "aurex",
                        "case_id": f"mcp-probe-{suffix}",
                        "batch_id": f"MCP-{suffix}",
                        "fact": (
                            "Column pressure drifted during sterility hold "
                            f"on MCP probe {suffix}."
                        ),
                        "source_uri": f"historian://aurex/mcp-probe/{suffix}",
                        "confidence": 0.9,
                        "fact_id": aurex_id,
                    },
                )
                if written.isError:
                    raise AssertionError(written.content)
                queried = await session.call_tool(
                    PRIOR_ART_QUERY,
                    {
                        "tenant_id": "aurex",
                        "case_tenant_id": "aurex",
                        "query": "column pressure drift sterility hold",
                        "top_k": 5,
                    },
                )
                if queried.isError:
                    raise AssertionError(queried.content)
                payload = queried.structuredContent or {}
                if payload.get("query_embedded") is not True:
                    raise AssertionError(
                        f"query_embedded is not true: {payload!r}"
                    )
                facts = payload.get("facts") or []
                ids = {
                    fact.get("fact_id")
                    for fact in facts
                    if isinstance(fact, dict)
                }
                if aurex_id not in ids:
                    raise AssertionError(
                        f"MCP KNN missed probe {aurex_id}; ids={ids}"
                    )
                print("MCP prior_art write/query ok (embedded, tenant-filtered)")

    # Cleanup via the same authenticated session helper after the first
    # session closes — call_mcp_tool does not send a bearer. Delete through
    # Firestore ADC instead so we do not leave probe facts.
    from app.domain.case_memory_store import FirestoreCaseMemoryStore

    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "countersign-505107")
    os.environ.pop("COUNTERSIGN_CASE_MEMORY_INMEMORY", None)
    store = FirestoreCaseMemoryStore()
    await store._collection().document(aurex_id).delete()
    print(f"probe document deleted ({aurex_id})")


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
        print(f"MCP Case Memory live verify failed: {exc}", file=sys.stderr)
        return 1
    print("Department MCP Case Memory live path ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
