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

"""Unit coverage for drive_fetch_evidence: MIME allow-list, size ceiling, errors.

The Drive API client is mocked; these tests do not make network calls. Live
Drive access (folder sharing, service-account impersonation) was verified
manually against the real API, not re-verified here.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from app.domain.department_models import DriveEvidencePayload
from app.domain.department_tools import DriveFetchError, drive_fetch_evidence


def _mock_service(*, meta: dict, media_bytes: bytes) -> MagicMock:
    service = MagicMock()
    files = service.files.return_value
    files.get.return_value.execute.return_value = meta
    files.get_media.return_value.execute.return_value = media_bytes
    return service


@patch("app.app_utils.services.get_google_credentials")
@patch("googleapiclient.discovery.build")
def test_drive_fetch_evidence_returns_base64_payload(
    mock_build: MagicMock, mock_creds: MagicMock
) -> None:
    media_bytes = b"fake-png-bytes"
    mock_build.return_value = _mock_service(
        meta={
            "id": "file-1",
            "mimeType": "image/png",
            "size": str(len(media_bytes)),
            "webViewLink": "https://drive.google.com/file/d/file-1/view",
        },
        media_bytes=media_bytes,
    )

    result = drive_fetch_evidence(tenant_id="aurex", file_id="file-1")

    assert isinstance(result, DriveEvidencePayload)
    assert result.file_id == "file-1"
    assert result.mime_type == "image/png"
    assert base64.b64decode(result.content_base64) == media_bytes
    assert result.source_uri == "https://drive.google.com/file/d/file-1/view"


@patch("app.app_utils.services.get_google_credentials")
@patch("googleapiclient.discovery.build")
def test_drive_fetch_evidence_falls_back_to_drive_uri_without_web_view_link(
    mock_build: MagicMock, mock_creds: MagicMock
) -> None:
    mock_build.return_value = _mock_service(
        meta={"id": "file-2", "mimeType": "application/pdf", "size": "10"},
        media_bytes=b"0123456789",
    )

    result = drive_fetch_evidence(tenant_id="aurex", file_id="file-2")

    assert result.source_uri == "drive://file-2"


@patch("app.app_utils.services.get_google_credentials")
@patch("googleapiclient.discovery.build")
def test_drive_fetch_evidence_rejects_disallowed_mime_type(
    mock_build: MagicMock, mock_creds: MagicMock
) -> None:
    mock_build.return_value = _mock_service(
        meta={
            "id": "file-3",
            "mimeType": "application/vnd.google-apps.document",
            "size": "10",
        },
        media_bytes=b"irrelevant",
    )

    with pytest.raises(DriveFetchError, match="not an accepted evidence type"):
        drive_fetch_evidence(tenant_id="aurex", file_id="file-3")


@patch("app.app_utils.services.get_google_credentials")
@patch("googleapiclient.discovery.build")
def test_drive_fetch_evidence_rejects_oversized_file(
    mock_build: MagicMock, mock_creds: MagicMock
) -> None:
    mock_build.return_value = _mock_service(
        meta={
            "id": "file-4",
            "mimeType": "image/png",
            "size": str(21 * 1024 * 1024),
        },
        media_bytes=b"irrelevant",
    )

    with pytest.raises(DriveFetchError, match="per-artifact limit"):
        drive_fetch_evidence(tenant_id="aurex", file_id="file-4")


@patch("app.app_utils.services.get_google_credentials")
@patch("googleapiclient.discovery.build")
def test_drive_fetch_evidence_wraps_http_errors(
    mock_build: MagicMock, mock_creds: MagicMock
) -> None:
    service = MagicMock()
    response = MagicMock(status=404, reason="Not Found")
    service.files.return_value.get.return_value.execute.side_effect = HttpError(
        response, b"not found"
    )
    mock_build.return_value = service

    with pytest.raises(DriveFetchError, match="Drive API call for file-5 failed"):
        drive_fetch_evidence(tenant_id="aurex", file_id="file-5")
