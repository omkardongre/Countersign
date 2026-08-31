# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.domain.department_source_openmaint import OpenMaintMaintenanceSource
from app.domain.department_tools import (
    CAPA_WRITE_TOOLS,
    OPENMAINT_CORRECTIVE_REQUEST,
    POST_QP_MAINTENANCE_TOOLS,
    SPONSOR_NOTIFICATION_REQUEST,
    CapaTenantMismatch,
    ErpNextCapaWriteError,
    openmaint_corrective_request,
)
from app.domain.post_qp_actions import (
    POST_QP_RESULT_STATE_KEY,
    execute_post_qp_actions,
    idempotency_key,
)
from app.domain.post_qp_models import (
    CorrectiveMaintenanceRequest,
    CorrectiveMaintenanceResult,
    SponsorNotificationRequest,
    SponsorNotificationResult,
)
from app.mcp import department_server as department_server
from app.sponsor_liaison import outbound as outbound_mod
from app.sponsor_liaison import server as sponsor_server
from tests.unit.test_capa_write import _build_reviewed


def _request(**overrides: str) -> CorrectiveMaintenanceRequest:
    values = {
        "tenant_id": "ACME01",
        "case_id": "CASE-001",
        "batch_id": "BATCH-001",
        "summary": "Server-built request",
        "idempotency_key": idempotency_key("ACME01", "CASE-001"),
        "lifecycle_state": "qp_reviewed",
    }
    values.update(overrides)
    return CorrectiveMaintenanceRequest(**values)


def test_idempotency_key_is_deterministic_and_scope_derived() -> None:
    first = idempotency_key("ACME01", "CASE-001")
    assert first == idempotency_key("ACME01", "CASE-001")
    assert first != idempotency_key("ACME01", "CASE-002")
    assert first != idempotency_key("OTHER", "CASE-001")


def test_post_qp_models_reject_decision_authority() -> None:
    with pytest.raises(ValueError, match="decision authority"):
        _request().model_copy(update={"decision_authority": "agent"})
        CorrectiveMaintenanceRequest(**(_request().model_dump() | {"decision_authority": "agent"}))


def test_openmaint_write_allow_list_is_independent() -> None:
    assert POST_QP_MAINTENANCE_TOOLS == {OPENMAINT_CORRECTIVE_REQUEST}
    assert POST_QP_MAINTENANCE_TOOLS.isdisjoint(CAPA_WRITE_TOOLS)


def test_openmaint_request_denies_tenant_mismatch() -> None:
    with pytest.raises(CapaTenantMismatch):
        openmaint_corrective_request(request=_request(), case_tenant_id="OTHER")


def test_openmaint_request_denies_pre_qp_lifecycle() -> None:
    with pytest.raises(ErpNextCapaWriteError, match="only after QP"):
        openmaint_corrective_request(
            request=_request(lifecycle_state="under_investigation"),
            case_tenant_id="ACME01",
        )


def test_openmaint_uses_rest_v3_process_request_convention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "COUNTERSIGN_OPENMAINT_CREDENTIALS_JSON",
        '{"ACME01":{"base_url":"https://maint.example.test","username":"service","password":"long-service-password","requester_role":"Requester"}}',
    )
    calls: list[tuple[str, str, dict | None]] = []

    class FakeErp:
        def maintenance_asset_binding(self, **kwargs):
            return SimpleNamespace(asset_code="EQ-1")

    def request_json(credentials, path, *, session_id=None, method="GET", body=None):
        calls.append((path, method, body))
        if path.startswith("sessions"):
            return {"success": True, "data": {"_id": "SESSION"}}
        if path.startswith("classes/Equipment"):
            return {"success": True, "data": [{"_id": 17, "Code": "EQ-1"}]}
        if method == "GET":
            return {"success": True, "data": []}
        return {"success": True, "data": {"_id": 41}}

    monkeypatch.setattr(
        "app.domain.department_source_erpnext.ErpNextSupplySource", FakeErp
    )
    monkeypatch.setattr(
        "app.domain.department_source_openmaint._request_json", request_json
    )
    result = OpenMaintMaintenanceSource().request_corrective_maintenance(
        request=_request()
    )
    assert result.work_order_id == "41"
    path, method, body = calls[-1]
    assert path == "processes/CorrectiveMaint/instances"
    assert method == "POST"
    assert body == {
        "ShortDescr": _request().idempotency_key,
        "ProcessNotes": "Server-built request",
        "CI": 17,
    }


@pytest.mark.asyncio
async def test_node_builds_payloads_and_preserves_output(monkeypatch: pytest.MonkeyPatch) -> None:
    reviewed = _build_reviewed()
    captured: dict[str, object] = {}

    async def maintenance(name: str, arguments: dict) -> dict:
        captured["name"] = name
        captured["maintenance"] = arguments
        return CorrectiveMaintenanceResult(
            work_order_id="WO-1",
            work_order_url="https://maint.example.test/ui/#processes/CorrectiveMaint/instances/1",
            was_existing=False,
            idempotency_key=arguments["idempotency_key"],
        ).model_dump(mode="json")

    async def sponsor(name: str, arguments: dict) -> dict:
        captured["sponsor_name"] = name
        captured["sponsor"] = arguments
        return SponsorNotificationResult(
            notification_id="N-1",
            was_existing=False,
            idempotency_key=arguments["idempotency_key"],
        ).model_dump(mode="json")

    monkeypatch.setattr("app.domain.post_qp_actions.call_post_qp_maintenance_tool", maintenance)
    monkeypatch.setattr("app.domain.post_qp_actions.call_post_qp_sponsor_tool", sponsor)
    ctx = SimpleNamespace(state={})
    output = await execute_post_qp_actions(ctx, reviewed)
    assert output is reviewed
    key = idempotency_key("ACME01", "CASE-001")
    assert captured["name"] == OPENMAINT_CORRECTIVE_REQUEST
    assert captured["sponsor_name"] == SPONSOR_NOTIFICATION_REQUEST
    assert captured["maintenance"]["lifecycle_state"] == "qp_reviewed"
    assert captured["maintenance"]["idempotency_key"] == key
    assert captured["sponsor"]["idempotency_key"] == key
    assert captured["sponsor"]["case_tenant_id"] == "ACME01"
    assert captured["sponsor"]["qp_recorded_at"] == "2026-08-30T10:00:00Z"
    result = ctx.state[POST_QP_RESULT_STATE_KEY]
    assert result["maintenance_error"] is None
    assert result["sponsor_notification_error"] is None
    assert result["decision_authority"] == "none"


@pytest.mark.asyncio
async def test_failures_are_independent_visible_and_non_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewed = _build_reviewed()

    async def maintenance(*args, **kwargs):
        raise RuntimeError("maintenance unavailable")

    async def sponsor(name: str, arguments: dict) -> dict:
        return SponsorNotificationResult(
            notification_id="N-1",
            was_existing=True,
            idempotency_key=arguments["idempotency_key"],
        ).model_dump(mode="json")

    monkeypatch.setattr("app.domain.post_qp_actions.call_post_qp_maintenance_tool", maintenance)
    monkeypatch.setattr("app.domain.post_qp_actions.call_post_qp_sponsor_tool", sponsor)
    ctx = SimpleNamespace(state={})
    assert await execute_post_qp_actions(ctx, reviewed) is reviewed
    result = ctx.state[POST_QP_RESULT_STATE_KEY]
    assert result["maintenance"] is None
    assert result["maintenance_error"] == {
        "action": "maintenance",
        "error_type": "RuntimeError",
        "message": "maintenance unavailable",
    }
    assert result["sponsor_notification"]["notification_id"] == "N-1"
    assert result["sponsor_notification_error"] is None


def _sponsor_request() -> SponsorNotificationRequest:
    return SponsorNotificationRequest(
        tenant_id="ACME01",
        case_id="CASE-001",
        batch_id="BATCH-001",
        qp_outcome="confirmed",
        qp_recorded_at=datetime.datetime(2026, 8, 30, tzinfo=datetime.UTC),
        idempotency_key=idempotency_key("ACME01", "CASE-001"),
        lifecycle_state="qp_reviewed",
    )


def test_sponsor_outbound_uses_a2a_message_shape_and_validates_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        outbound_mod.SPONSOR_OUTBOUND_TARGETS_ENV,
        json.dumps({"ACME01": {"url": "https://sponsor.example.test/a2a", "bearer_token": "sponsor-secret-token"}}),
    )
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            captured["read_size"] = size
            return json.dumps({"jsonrpc": "2.0", "id": _sponsor_request().idempotency_key, "result": {"id": "task-1", "was_existing": True}}).encode()

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(outbound_mod.urllib.request, "urlopen", urlopen)
    result = outbound_mod.send_sponsor_notification(_sponsor_request())
    body = json.loads(captured["request"].data)
    assert body["method"] == "message/send"
    assert body["params"]["message"]["role"] == "ROLE_USER"
    assert body["params"]["message"]["messageId"] == _sponsor_request().idempotency_key
    assert body["params"]["message"]["parts"][0]["kind"] == "data"
    assert captured["read_size"] == outbound_mod._MAX_RESPONSE_BYTES + 1
    assert result.notification_id == "task-1"
    assert result.was_existing is True


def test_sponsor_outbound_rejects_oversized_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        outbound_mod.SPONSOR_OUTBOUND_TARGETS_ENV,
        json.dumps({"ACME01": {"url": "https://sponsor.example.test/a2a", "bearer_token": "sponsor-secret-token"}}),
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            return b"x" * size

    monkeypatch.setattr(outbound_mod.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(outbound_mod.SponsorOutboundError, match="size limit"):
        outbound_mod.send_sponsor_notification(_sponsor_request())


def test_department_mcp_relay_uses_ambient_oidc_and_preserves_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    liaison_url = "https://liaison.example.test"
    monkeypatch.setenv(department_server.SPONSOR_LIAISON_URL_ENV, liaison_url)
    captured = {}
    def fetch_id_token(request, audience):
        captured["audience"] = audience
        return "token"

    monkeypatch.setattr(department_server.id_token, "fetch_id_token", fetch_id_token)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            captured["read_size"] = size
            return SponsorNotificationResult(
                notification_id="task-1",
                was_existing=False,
                idempotency_key=_sponsor_request().idempotency_key,
            ).model_dump_json().encode()

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(department_server.urllib.request, "urlopen", urlopen)
    result = department_server._relay_sponsor_notification(_sponsor_request())
    assert result.notification_id == "task-1"
    assert captured["audience"] == liaison_url
    assert captured["request"].full_url == (
        liaison_url + department_server.SPONSOR_NOTIFICATION_PATH
    )
    assert captured["request"].get_header("Authorization") == "Bearer token"
    assert json.loads(captured["request"].data)["tenant_id"] == "ACME01"


def test_department_mcp_relay_rejects_mismatched_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        department_server.SPONSOR_LIAISON_URL_ENV, "https://liaison.example.test"
    )
    monkeypatch.setattr(department_server.id_token, "fetch_id_token", lambda *args: "token")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            return SponsorNotificationResult(
                notification_id="task-1",
                was_existing=False,
                idempotency_key="different-idempotency-key",
            ).model_dump_json().encode()

    monkeypatch.setattr(
        department_server.urllib.request, "urlopen", lambda *args, **kwargs: Response()
    )
    with pytest.raises(
        department_server.SponsorNotificationRelayError, match="mismatched"
    ):
        department_server._relay_sponsor_notification(_sponsor_request())


def test_sponsor_internal_endpoint_requires_verified_allowed_oidc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = "case-invoker@example.iam.gserviceaccount.com"
    monkeypatch.setenv(sponsor_server.CASE_CALLERS_ENV, caller)
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token",
        lambda token, request, audience: {"email": caller, "email_verified": True, "aud": audience},
    )
    monkeypatch.setattr(
        sponsor_server,
        "send_sponsor_notification",
        lambda payload: SponsorNotificationResult(notification_id="task-1", was_existing=False, idempotency_key=payload.idempotency_key),
    )
    client = TestClient(sponsor_server.build_app())
    denied = client.post(sponsor_server.NOTIFICATION_PATH, json=_sponsor_request().model_dump(mode="json"))
    assert denied.status_code == 403
    allowed = client.post(
        sponsor_server.NOTIFICATION_PATH,
        headers={"Authorization": "Bearer oidc-token"},
        json=_sponsor_request().model_dump(mode="json"),
    )
    assert allowed.status_code == 200
    assert allowed.json()["notification_id"] == "task-1"


def test_sponsor_internal_endpoint_denies_unverified_oidc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = "case-invoker@example.iam.gserviceaccount.com"
    monkeypatch.setenv(sponsor_server.CASE_CALLERS_ENV, caller)
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token",
        lambda *args, **kwargs: {"email": caller, "email_verified": False},
    )
    response = TestClient(sponsor_server.build_app()).post(
        sponsor_server.NOTIFICATION_PATH,
        headers={"Authorization": "Bearer oidc-token"},
        json=_sponsor_request().model_dump(mode="json"),
    )
    assert response.status_code == 403
