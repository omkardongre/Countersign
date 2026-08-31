# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import datetime
import json

import pytest
from fastapi.testclient import TestClient

from app.domain.post_qp_actions import idempotency_key
from app.domain.post_qp_models import SponsorNotificationRequest
from app.sponsor_qa import auth
from app.sponsor_qa import server as qa_server
from app.sponsor_qa.store import reset_notification_store_cache

TOKEN = "tenant-a-secret-token-long"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COUNTERSIGN_SPONSOR_QA_INMEMORY", "1")
    monkeypatch.setenv(
        auth.AUTH_SECRET_ENV,
        json.dumps([
            {"tenant_id": "tenant-a", "bearer_token": TOKEN},
            {"tenant_id": "tenant-b", "bearer_token": "tenant-b-secret-token-long"},
        ]),
    )
    reset_notification_store_cache()
    yield TestClient(qa_server.build_app())
    reset_notification_store_cache()


def payload(tenant_id: str = "tenant-a", **updates) -> SponsorNotificationRequest:
    values = {
        "tenant_id": tenant_id,
        "case_id": "CASE-1",
        "batch_id": "BATCH-1",
        "qp_outcome": "confirmed",
        "qp_recorded_at": datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC),
        "idempotency_key": idempotency_key(tenant_id, "CASE-1"),
        "lifecycle_state": "qp_reviewed",
    }
    values.update(updates)
    return SponsorNotificationRequest(**values)


def rpc(item: SponsorNotificationRequest) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": item.idempotency_key,
        "method": "message/send",
        "params": {"message": {
            "messageId": item.idempotency_key,
            "role": "ROLE_USER",
            "parts": [{"kind": "data", "data": item.model_dump(mode="json")}],
        }},
    }


def headers(item: SponsorNotificationRequest, token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": item.idempotency_key}


def test_health_and_agent_card_are_public(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    card = client.get(qa_server.CARD_PATH).json()
    assert card["url"].endswith(qa_server.RPC_PATH)
    assert card["skills"][0]["id"] == "receive_post_qp_notification"


def test_receiver_requires_authentication(client: TestClient) -> None:
    item = payload()
    assert client.post(qa_server.RPC_PATH, json=rpc(item)).status_code == 401
    assert client.get("/notifications").status_code == 401


def test_create_and_duplicate_reuse_are_deterministic(client: TestClient) -> None:
    item = payload()
    first = client.post(qa_server.RPC_PATH, json=rpc(item), headers=headers(item))
    second = client.post(qa_server.RPC_PATH, json=rpc(item), headers=headers(item))
    assert first.status_code == second.status_code == 200
    assert first.json()["result"]["id"] == second.json()["result"]["id"]
    assert first.json()["result"]["was_existing"] is False
    assert second.json()["result"]["was_existing"] is True


def test_conflicting_duplicate_is_rejected(client: TestClient) -> None:
    item = payload()
    assert client.post(qa_server.RPC_PATH, json=rpc(item), headers=headers(item)).status_code == 200
    conflict = payload(batch_id="DIFFERENT")
    response = client.post(qa_server.RPC_PATH, json=rpc(conflict), headers=headers(conflict))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == -32009


def test_cross_tenant_payload_is_denied_and_not_visible(client: TestClient) -> None:
    other = payload("tenant-b")
    response = client.post(qa_server.RPC_PATH, json=rpc(other), headers=headers(other))
    assert response.status_code == 403
    page = client.get("/notifications", headers={"Authorization": f"Bearer {TOKEN}"})
    assert "BATCH-1" not in page.text


def test_page_is_read_only_scoped_and_html_escaped(client: TestClient) -> None:
    item = payload(batch_id="<script>alert(1)</script>")
    assert client.post(qa_server.RPC_PATH, json=rpc(item), headers=headers(item)).status_code == 200
    page = client.get("/notifications", headers={"Authorization": f"Bearer {TOKEN}"})
    assert page.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "<script>alert(1)</script>" not in page.text
    assert client.post("/notifications", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 405


def test_identifier_and_lifecycle_validation(client: TestClient) -> None:
    item = payload(lifecycle_state="under_investigation")
    response = client.post(qa_server.RPC_PATH, json=rpc(item), headers=headers(item))
    assert response.status_code == 400
    valid = payload()
    response = client.post(
        qa_server.RPC_PATH,
        json=rpc(valid),
        headers={"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": "wrong"},
    )
    assert response.status_code == 400
