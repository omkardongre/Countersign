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

"""Session backend selection: Cloud SQL first, Vertex only if explicitly allowed."""

from __future__ import annotations

import pytest

from app.app_utils.services import get_session_service


def test_runtime_without_cloud_sql_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "123")
    get_session_service.cache_clear()
    with pytest.raises(RuntimeError, match="INSTANCE_CONNECTION_NAME"):
        get_session_service()
    get_session_service.cache_clear()


def test_cloud_sql_requires_iam_user(monkeypatch) -> None:
    from app.app_utils.cloud_sql import iam_db_user

    monkeypatch.setenv(
        "INSTANCE_CONNECTION_NAME",
        "countersign-505107:us-central1:countersign-sessions",
    )
    with pytest.raises(RuntimeError, match="DB_IAM_USER"):
        iam_db_user()
