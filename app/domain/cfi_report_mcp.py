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

"""Tenant-bound CFI Docs/Drive report client for the CFI Runtime."""

from __future__ import annotations

from app.domain.cfi_artifact_models import CfiReportWriteResult
from app.domain.department_tools import CFI_REPORT_WRITE
from app.mcp.client import call_cfi_artifact_tool


def _unwrap(payload: dict) -> dict:
    if "result" in payload and "document_id" not in payload:
        return payload["result"]
    return payload


class McpCfiReportWriter:
    async def write_async(
        self, *, tenant_id: str, cfi_id: str, title: str, body: str
    ) -> CfiReportWriteResult:
        payload = await call_cfi_artifact_tool(
            CFI_REPORT_WRITE,
            {
                "tenant_id": tenant_id,
                "case_tenant_id": tenant_id,
                "cfi_id": cfi_id,
                "title": title,
                "body": body,
            },
        )
        return CfiReportWriteResult.model_validate(_unwrap(payload or {}))
