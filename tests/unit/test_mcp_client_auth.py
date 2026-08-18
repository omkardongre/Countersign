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

"""``app/mcp/client.py`` auth-header behaviour.

Guards the fix for a live bug: an Agent Identity's ambient credentials mint a
Workload-Identity-Federation JWT (issuer sts.googleapis.com, subject
spiffe://..., no email claim), which Cloud Run's invoker check rejects with
401 "The access token could not be verified" even though the identity holds
roles/run.invoker. The fix is impersonating a real service account via
COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT rather than presenting the Agent
Identity's own token directly.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.mcp.client import (
    MCP_INVOKER_SERVICE_ACCOUNT_ENV,
    _mcp_auth_headers,
    cloud_run_audience,
)


def test_cloud_run_audience_drops_path() -> None:
    assert (
        cloud_run_audience("https://svc-777.us-central1.run.app/mcp")
        == "https://svc-777.us-central1.run.app"
    )


def test_loopback_targets_get_no_auth_header() -> None:
    assert _mcp_auth_headers("http://127.0.0.1:9000/mcp") == {}
    assert _mcp_auth_headers("http://localhost:9000/mcp") == {}


def test_impersonates_configured_service_account(monkeypatch) -> None:
    """When COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT is set, the token must be
    minted through impersonated_credentials.IDTokenCredentials, not the
    ambient identity's own fetch_id_token — that path is exactly what a live
    Agent Identity 401 traced back to."""
    monkeypatch.setenv(
        MCP_INVOKER_SERVICE_ACCOUNT_ENV,
        "countersign-case-invoker@countersign-505107.iam.gserviceaccount.com",
    )

    with (
        patch("google.auth.default", return_value=(object(), "countersign-505107")),
        patch("app.mcp.client.id_token.fetch_id_token") as fetch_mock,
        patch(
            "app.mcp.client.impersonated_credentials.Credentials"
        ) as impersonated_cls,
        patch(
            "app.mcp.client.impersonated_credentials.IDTokenCredentials"
        ) as id_creds_cls,
    ):
        id_creds_instance = id_creds_cls.return_value
        id_creds_instance.token = "impersonated-token"

        headers = _mcp_auth_headers(
            "https://countersign-department-mcp-777.us-central1.run.app/mcp"
        )

        assert headers == {"Authorization": "Bearer impersonated-token"}

        # First: build the impersonated Credentials for the invoker SA.
        impersonated_cls.assert_called_once()
        _args, impersonate_kwargs = impersonated_cls.call_args
        assert impersonate_kwargs["target_principal"] == (
            "countersign-case-invoker@countersign-505107.iam.gserviceaccount.com"
        )

        # Then: wrap it in IDTokenCredentials for the actual Cloud Run audience.
        id_creds_cls.assert_called_once()
        id_creds_args, id_creds_kwargs = id_creds_cls.call_args
        assert id_creds_args[0] is impersonated_cls.return_value
        assert id_creds_kwargs["target_audience"] == (
            "https://countersign-department-mcp-777.us-central1.run.app"
        )

        id_creds_instance.refresh.assert_called_once()
        fetch_mock.assert_not_called()


def test_falls_back_to_ambient_identity_without_invoker_service_account(
    monkeypatch,
) -> None:
    """No configured invoker SA: use the ambient identity's own ID token.
    This is the path that fails closed with a Cloud Run 401 for an Agent
    Identity, but must still work unchanged for a normal service account."""
    monkeypatch.delenv(MCP_INVOKER_SERVICE_ACCOUNT_ENV, raising=False)

    with patch("app.mcp.client.id_token.fetch_id_token", return_value="ambient-token") as fetch_mock:
        headers = _mcp_auth_headers(
            "https://countersign-department-mcp-777.us-central1.run.app/mcp"
        )

    assert headers == {"Authorization": "Bearer ambient-token"}
    fetch_mock.assert_called_once()
    args, _ = fetch_mock.call_args
    assert args[1] == "https://countersign-department-mcp-777.us-central1.run.app"


def test_mint_failure_raises_department_tool_call_error(monkeypatch) -> None:
    from app.mcp.client import DepartmentToolCallError

    monkeypatch.delenv(MCP_INVOKER_SERVICE_ACCOUNT_ENV, raising=False)
    with patch(
        "app.mcp.client.id_token.fetch_id_token", side_effect=RuntimeError("boom")
    ):
        with pytest.raises(DepartmentToolCallError, match="Could not mint"):
            _mcp_auth_headers(
                "https://countersign-department-mcp-777.us-central1.run.app/mcp"
            )
