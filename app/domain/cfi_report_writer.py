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

"""Department-MCP Google Docs/Drive writer for CFI reports.

This is deliberately a separate function and appProperties namespace from
``qms_report_write``. The CFI report cannot be used to exercise QMS/release
authority. Repeated calls locate the same tenant/CFI document and update it
in place; they never create a second report for a retry of the same CFI.
"""

from __future__ import annotations

import json
import os
from typing import Any

from app.app_utils.services import (
    get_delegated_google_credentials,
    get_dwd_subject,
)
from app.domain.cfi_artifact_models import CfiReportWriteResult

FOLDER_MAP_ENV = "COUNTERSIGN_CFI_REPORTS_FOLDER_MAP_JSON"
TENANT_PROPERTY = "countersign_cfi_tenant_id"
CFI_PROPERTY = "countersign_cfi_id"


class CfiReportWriteError(RuntimeError):
    """The CFI Docs/Drive write failed or its folder policy is missing."""


def _folder_for_tenant(tenant_id: str) -> str:
    raw = os.environ.get(FOLDER_MAP_ENV, "").strip()
    if not raw:
        raise CfiReportWriteError(
            f"{FOLDER_MAP_ENV} is required; refusing to file a CFI report "
            "without a tenant-locked Drive folder."
        )
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CfiReportWriteError(f"{FOLDER_MAP_ENV} is not valid JSON: {exc}") from exc
    if not isinstance(mapping, dict) or not mapping:
        raise CfiReportWriteError(f"{FOLDER_MAP_ENV} must be a non-empty JSON object.")
    folder = mapping.get(tenant_id)
    if not isinstance(folder, str) or not folder.strip():
        raise CfiReportWriteError(
            f"{FOLDER_MAP_ENV} has no folder for tenant {tenant_id!r}."
        )
    return folder.strip()


def _replace_document(docs: Any, document_id: str, body: str) -> None:
    document = docs.documents().get(documentId=document_id).execute()
    content = document.get("body", {}).get("content", [])
    end_index = content[-1].get("endIndex", 1) if content else 1
    requests: list[dict[str, Any]] = []
    if end_index > 1:
        requests.append(
            {
                "deleteContentRange": {
                    "range": {"startIndex": 1, "endIndex": end_index - 1}
                }
            }
        )
    requests.append({"insertText": {"location": {"index": 1}, "text": body}})
    docs.documents().batchUpdate(
        documentId=document_id, body={"requests": requests}
    ).execute()


def write_cfi_report(
    *, tenant_id: str, cfi_id: str, title: str, body: str
) -> CfiReportWriteResult:
    """Create or update one tenant/CFI Google Doc idempotently."""
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    folder_id = _folder_for_tenant(tenant_id)
    subject = get_dwd_subject()
    if not subject:
        raise CfiReportWriteError(
            "CFI report filing requires Workspace DWD: service accounts have "
            "no My Drive storage quota. Set COUNTERSIGN_DWD_SUBJECT and "
            "authorize Drive/Docs scopes for the signer service account."
        )
    try:
        credentials = get_delegated_google_credentials(
            scopes=[
                "https://www.googleapis.com/auth/documents",
                "https://www.googleapis.com/auth/drive",
            ],
            subject=subject,
        )
    except Exception as exc:
        raise CfiReportWriteError(
            "Unable to create Workspace DWD credentials for the CFI report: "
            f"{exc}"
        ) from exc
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    docs = build("docs", "v1", credentials=credentials, cache_discovery=False)
    query = (
        f"'{folder_id}' in parents and trashed = false and "
        f"appProperties has {{key='{TENANT_PROPERTY}' and value='{tenant_id}'}} and "
        f"appProperties has {{key='{CFI_PROPERTY}' and value='{cfi_id}'}}"
    )
    try:
        existing = (
            drive.files()
            .list(q=query, fields="files(id,webViewLink,name)", pageSize=10)
            .execute()
            .get("files", [])
        )
        if len(existing) > 1:
            raise CfiReportWriteError(
                f"Multiple CFI reports found for tenant/CFI {tenant_id}/{cfi_id}."
            )
        if existing:
            document_id = existing[0]["id"]
            _replace_document(docs, document_id, body)
            drive.files().update(
                fileId=document_id,
                body={"name": title},
                fields="id,webViewLink",
            ).execute()
            return CfiReportWriteResult(
                document_id=document_id,
                document_url=existing[0].get("webViewLink")
                or f"https://docs.google.com/document/d/{document_id}/edit",
                was_update=True,
            )

        created = docs.documents().create(body={"title": title}).execute()
        document_id = created["documentId"]
        docs.documents().batchUpdate(
            documentId=document_id,
            body={"requests": [{"insertText": {"location": {"index": 1}, "text": body}}]},
        ).execute()
        moved = drive.files().update(
            fileId=document_id,
            addParents=folder_id,
            body={
                "appProperties": {
                    TENANT_PROPERTY: tenant_id,
                    CFI_PROPERTY: cfi_id,
                }
            },
            fields="id,webViewLink",
        ).execute()
        return CfiReportWriteResult(
            document_id=document_id,
            document_url=moved.get("webViewLink")
            or f"https://docs.google.com/document/d/{document_id}/edit",
            was_update=False,
        )
    except CfiReportWriteError:
        raise
    except HttpError as exc:
        detail = getattr(exc, "content", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise CfiReportWriteError(
            f"Google Docs/Drive CFI report write failed with HTTP "
            f"{getattr(exc.resp, 'status', 'unknown')}: {str(detail)[:1000]}"
        ) from exc
