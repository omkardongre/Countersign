# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Governed outbound sponsor notification owned by the liaison service."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.post_qp_models import (
    SponsorNotificationRequest,
    SponsorNotificationResult,
)

SPONSOR_OUTBOUND_TARGETS_ENV = "COUNTERSIGN_SPONSOR_OUTBOUND_TARGETS_JSON"
_MAX_RESPONSE_BYTES = 512 * 1024


class SponsorOutboundError(Exception):
    pass


class _Target(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=2048)
    bearer_token: str = Field(min_length=16, max_length=4096)

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
            raise ValueError("Sponsor A2A URL must be HTTPS without userinfo.")
        return value


def _target(tenant_id: str) -> _Target:
    raw = os.environ.get(SPONSOR_OUTBOUND_TARGETS_ENV, "").strip()
    try:
        return _Target.model_validate(json.loads(raw)[tenant_id])
    except (KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        raise SponsorOutboundError(
            f"No valid outbound sponsor target is configured for tenant {tenant_id!r}."
        ) from exc


def send_sponsor_notification(
    request: SponsorNotificationRequest,
) -> SponsorNotificationResult:
    if request.lifecycle_state != "qp_reviewed":
        raise SponsorOutboundError("Sponsor notification is allowed only after QP review.")
    target = _target(request.tenant_id)
    message = {
        "jsonrpc": "2.0",
        "id": request.idempotency_key,
        "method": "message/send",
        "params": {
            "message": {
                "messageId": request.idempotency_key,
                "role": "ROLE_USER",
                "parts": [
                    {
                        "kind": "data",
                        "data": request.model_dump(mode="json"),
                    }
                ],
            }
        },
    }
    http_request = urllib.request.Request(
        target.url,
        data=json.dumps(message, separators=(",", ":")).encode(),
        headers={
            "Authorization": f"Bearer {target.bearer_token}",
            "Content-Type": "application/json",
            "Idempotency-Key": request.idempotency_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=15) as response:
            response_body = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise SponsorOutboundError(
            f"Sponsor A2A peer returned HTTP {exc.code}."
        ) from exc
    except urllib.error.URLError as exc:
        raise SponsorOutboundError("Outbound sponsor A2A transport failed.") from exc
    if len(response_body) > _MAX_RESPONSE_BYTES:
        raise SponsorOutboundError("Sponsor A2A response exceeded the size limit.")
    try:
        payload: Any = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SponsorOutboundError("Sponsor A2A peer returned invalid JSON.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("jsonrpc") != "2.0"
        or payload.get("id") != request.idempotency_key
    ):
        raise SponsorOutboundError("Sponsor A2A peer returned an invalid JSON-RPC response.")
    if payload.get("error") is not None:
        raise SponsorOutboundError("Sponsor A2A peer rejected the notification.")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise SponsorOutboundError("Sponsor A2A peer returned no task or message result.")
    notification_id = result.get("id") or result.get("messageId")
    was_existing = result.get("was_existing", False)
    if not isinstance(notification_id, str) or not notification_id:
        raise SponsorOutboundError("Sponsor A2A result had no identifier.")
    if not isinstance(was_existing, bool):
        raise SponsorOutboundError("Sponsor A2A idempotency result was invalid.")
    return SponsorNotificationResult(
        notification_id=notification_id,
        was_existing=was_existing,
        idempotency_key=request.idempotency_key,
    )
