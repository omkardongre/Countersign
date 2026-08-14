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

"""Redact one poisoned or incorrect Case Memory fact. Operator-facing.

``prior_art_redact`` is deliberately not on the case-workflow Gateway
allow-list (``mcp-case-workflow-tools-allow.yaml``): the case workflow reads
and writes Case Memory on its own behalf, but redaction is a human decision
about a fact's correctness, not something a case should be able to trigger
for itself. This CLI is the operator entry point for that tool, run against
the department MCP process directly (the same ``COUNTERSIGN_MCP_URL`` /
``COUNTERSIGN_MCP_INPROCESS`` selection every other MCP caller uses).
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.domain.department_tools import PRIOR_ART_REDACT
from app.mcp.client import call_case_memory_tool


async def redact_fact(*, tenant_id: str, fact_id: str, reason: str) -> dict:
    payload = await call_case_memory_tool(
        PRIOR_ART_REDACT,
        {
            "tenant_id": tenant_id,
            "fact_id": fact_id,
            "reason": reason,
            "case_tenant_id": tenant_id,
        },
    )
    return payload or {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Redact one Case Memory fact by id, scoped to its tenant."
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--fact-id", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    result = asyncio.run(
        redact_fact(
            tenant_id=args.tenant_id, fact_id=args.fact_id, reason=args.reason
        )
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
