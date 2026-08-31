# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""External, production-shaped Sponsor QA A2A notification receiver."""

from __future__ import annotations

import html
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.domain.post_qp_models import SponsorNotificationRequest
from app.sponsor_qa.auth import authenticate
from app.sponsor_qa.store import NotificationConflictError, get_notification_store

RPC_PATH = "/a2a/sponsor-qa"
CARD_PATH = "/.well-known/agent-card.json"


def _app_url() -> str:
    return os.environ.get("APP_URL", "http://0.0.0.0:8082").rstrip("/")


def agent_card() -> dict[str, Any]:
    return {
        "name": "Sponsor QA Notification Receiver",
        "description": "Receives tenant-scoped post-QP notifications from the CDMO sponsor liaison.",
        "version": os.environ.get("AGENT_VERSION", "0.1.0"),
        "protocolVersion": "0.3.0",
        "url": f"{_app_url()}{RPC_PATH}",
        "preferredTransport": "JSONRPC",
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "capabilities": {"streaming": False, "pushNotifications": False},
        "skills": [{
            "id": "receive_post_qp_notification",
            "name": "Receive post-QP notification",
            "description": "Durably records an authorized sponsor tenant's QP outcome notification.",
            "tags": ["quality", "notification", "post-qp"],
            "examples": ["Record the QP outcome for my tenant's batch."],
        }],
    }


def _rpc_error(request_id: Any, code: int, message: str, *, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
    )


def _notification_payload(body: Any, tenant_id: str, idempotency_header: str | None) -> tuple[Any, SponsorNotificationRequest]:
    if not isinstance(body, dict):
        raise ValueError("JSON-RPC request must be an object.")
    request_id = body.get("id")
    if body.get("jsonrpc") != "2.0" or body.get("method") != "message/send":
        raise ValueError("Only JSON-RPC message/send is supported.")
    message = body.get("params", {}).get("message", {})
    parts = message.get("parts", [])
    data_parts = [part.get("data") for part in parts if isinstance(part, dict) and part.get("kind") == "data"]
    if len(data_parts) != 1:
        raise ValueError("Exactly one data part is required.")
    payload = SponsorNotificationRequest.model_validate(data_parts[0])
    if payload.lifecycle_state != "qp_reviewed":
        raise ValueError("Only post-QP notifications are accepted.")
    if payload.tenant_id != tenant_id:
        raise PermissionError("Authenticated tenant does not match notification tenant.")
    if request_id != payload.idempotency_key or message.get("messageId") != payload.idempotency_key:
        raise ValueError("JSON-RPC, message, and payload identifiers must match.")
    if idempotency_header != payload.idempotency_key:
        raise ValueError("Idempotency-Key must match the notification identifier.")
    return request_id, payload


def build_app() -> FastAPI:
    app = FastAPI(title="countersign-sponsor-qa", docs_url=None, redoc_url=None)

    @app.get(CARD_PATH)
    async def get_agent_card() -> dict[str, Any]:
        return agent_card()

    @app.post(RPC_PATH)
    async def receive_notification(request: Request):
        tenant_id = authenticate(request)
        try:
            body = await request.json()
        except Exception:
            return _rpc_error(None, -32700, "Invalid JSON.", status=400)
        request_id = body.get("id") if isinstance(body, dict) else None
        try:
            request_id, payload = _notification_payload(
                body, tenant_id, request.headers.get("idempotency-key")
            )
            record, was_existing = await run_in_threadpool(
                get_notification_store().create_or_get, payload
            )
        except PermissionError as exc:
            return _rpc_error(request_id, -32003, str(exc), status=403)
        except (ValueError, ValidationError) as exc:
            return _rpc_error(request_id, -32602, str(exc), status=400)
        except NotificationConflictError as exc:
            return _rpc_error(request_id, -32009, str(exc), status=409)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "id": record.notification_id,
                "messageId": record.notification_id,
                "was_existing": was_existing,
                "tenant_id": tenant_id,
            },
        }

    @app.get("/notifications", response_class=HTMLResponse)
    async def notification_page(request: Request) -> str:
        tenant_id = authenticate(request)
        records = await run_in_threadpool(
            get_notification_store().list_for_tenant, tenant_id
        )
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(item.batch_id)}</td>"
            f"<td>{html.escape(item.case_id)}</td>"
            f"<td>{html.escape(item.qp_outcome)}</td>"
            f"<td>{html.escape(item.qp_recorded_at.isoformat())}</td>"
            f"<td>{html.escape(item.notification_id)}</td>"
            "</tr>"
            for item in records
        ) or '<tr><td colspan="5">No notifications received.</td></tr>'
        return f"""<!doctype html><html><head><title>Sponsor QA notifications</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:3rem auto;padding:0 1rem;color:#202124}}table{{border-collapse:collapse;width:100%}}th,td{{padding:.75rem;border-bottom:1px solid #dadce0;text-align:left}}th{{background:#f8f9fa}}</style></head>
<body><h1>Sponsor QA notifications</h1><p>Tenant: <strong>{html.escape(tenant_id)}</strong></p>
<table><thead><tr><th>Batch</th><th>Case</th><th>QP outcome</th><th>QP recorded</th><th>Notification</th></tr></thead><tbody>{rows}</tbody></table></body></html>"""

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "server": "countersign-sponsor-qa"}

    return app


app = build_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8082")))


if __name__ == "__main__":
    main()
