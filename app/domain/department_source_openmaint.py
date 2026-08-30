# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Read-only, tenant-scoped openMAINT connector.

The connector receives an equipment identifier only from the ERPNext
work-order binding endpoint.  It never accepts an equipment id from an agent
or case form, and it authenticates to openMAINT with a separate service user.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.department_models import MaintenanceSnapshot, MaintenanceWorkOrder
from app.domain.department_source import DepartmentRecordMissing

OPENMAINT_CREDENTIALS_ENV = "COUNTERSIGN_OPENMAINT_CREDENTIALS_JSON"
OPENMAINT_TIMEOUT_ENV = "COUNTERSIGN_OPENMAINT_TIMEOUT_SECONDS"
BACKEND_OPENMAINT = "openmaint"
_DEFAULT_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 512 * 1024


class OpenMaintApiError(Exception):
    """openMAINT transport or response-contract failure."""


class _CredentialPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_url: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=16, max_length=1024)

    @field_validator("base_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("openMAINT base_url must be an HTTPS origin/path")
        if parsed.username or parsed.password:
            raise ValueError("openMAINT base_url must not contain userinfo")
        return value.rstrip("/")


@dataclass(frozen=True)
class OpenMaintCredentials:
    base_url: str
    username: str
    password: str


def _timeout_seconds() -> float:
    raw = os.environ.get(OPENMAINT_TIMEOUT_ENV, str(_DEFAULT_TIMEOUT_SECONDS))
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{OPENMAINT_TIMEOUT_ENV} must be numeric.") from exc
    if not 1 <= value <= 60:
        raise RuntimeError(f"{OPENMAINT_TIMEOUT_ENV} must be between 1 and 60.")
    return value


def credentials_for_tenant(tenant_id: str) -> OpenMaintCredentials:
    raw = os.environ.get(OPENMAINT_CREDENTIALS_ENV, "").strip()
    if not raw:
        raise RuntimeError(f"{OPENMAINT_CREDENTIALS_ENV} is not configured.")
    try:
        payload = json.loads(raw)
        value = payload[tenant_id]
        parsed = _CredentialPayload.model_validate(value)
    except (KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(
            f"No valid openMAINT credentials are configured for tenant {tenant_id!r}."
        ) from exc
    return OpenMaintCredentials(**parsed.model_dump())


def _request_json(
    credentials: OpenMaintCredentials,
    path: str,
    *,
    session_id: str | None = None,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{credentials.base_url}/services/rest/v3/{path.lstrip('/')}"
    basic = base64.b64encode(
        f"{credentials.username}:{credentials.password}".encode("utf-8")
    ).decode("ascii")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {basic}",
    }
    if session_id:
        headers["CMDBuild-Authorization"] = session_id
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            response_body = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise OpenMaintApiError(f"openMAINT {path} failed with HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise OpenMaintApiError("openMAINT transport failed.") from exc
    if len(response_body) > _MAX_RESPONSE_BYTES:
        raise OpenMaintApiError("openMAINT response exceeded the size limit.")
    try:
        payload = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenMaintApiError("openMAINT returned invalid JSON.") from exc
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise OpenMaintApiError(f"openMAINT {path} returned an unsuccessful response.")
    return payload


def _parse_datetime(value: Any) -> datetime.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.UTC)


class OpenMaintMaintenanceSource:
    backend_name = BACKEND_OPENMAINT

    def maintenance(self, *, tenant_id: str, asset_code: str) -> MaintenanceSnapshot:
        credentials = credentials_for_tenant(tenant_id)
        session = _request_json(
            credentials,
            "sessions?scope=service&returnId=true",
            method="POST",
            body={"username": credentials.username, "password": credentials.password},
        )
        session_id = str((session.get("data") or {}).get("_id") or "")
        if not session_id:
            raise OpenMaintApiError("openMAINT did not return a service session.")

        equipment = _request_json(
            credentials, "classes/Equipment/cards?scope=service&limit=100", session_id=session_id
        ).get("data", [])
        asset = next(
            (row for row in equipment if isinstance(row, dict) and row.get("Code") == asset_code),
            None,
        )
        if asset is None:
            raise DepartmentRecordMissing(f"openMAINT equipment {asset_code!r} was not found.")
        asset_id = str(asset.get("_id") or "")
        if not asset_id:
            raise OpenMaintApiError("openMAINT equipment record has no card id.")

        plans = _request_json(
            credentials, "classes/PrevMaintConfig/cards?scope=service&limit=100", session_id=session_id
        ).get("data", [])
        matching_plans = [
            row for row in plans
            if isinstance(row, dict)
            and (str(row.get("CISubset") or "") == asset_id or asset_code in str(row))
        ]
        due_dates = [
            _parse_datetime(row.get("FirstDueExecDate")) for row in matching_plans
        ]
        preventive_due_at = min((value for value in due_dates if value), default=None)

        processes = _request_json(
            credentials, "processes/CorrectiveMaint/instances?scope=service&limit=100", session_id=session_id
        ).get("data", [])
        work_orders: list[MaintenanceWorkOrder] = []
        for row in processes:
            if not isinstance(row, dict) or str(row.get("CISubset") or "") != asset_id:
                continue
            record_id = str(row.get("_id") or "")
            if not record_id:
                continue
            flow = str(row.get("_FlowStatus_code") or row.get("Status") or "")
            closed = flow.startswith("closed") or "Completed" in flow
            work_orders.append(
                MaintenanceWorkOrder(
                    record_id=record_id,
                    process_number=str(row.get("Code") or record_id),
                    status=flow or "unknown",
                    opened_at=_parse_datetime(row.get("OpeningDate")),
                    closed_at=_parse_datetime(row.get("ClosureDate")),
                    closed=closed,
                    source_uri=f"{credentials.base_url}/ui/#processes/CorrectiveMaint/instances/{record_id}",
                )
            )
        source_uri = f"{credentials.base_url}/ui/#classes/Equipment/cards/{asset_id}"
        return MaintenanceSnapshot(
            source_uri=source_uri,
            asset_code=asset_code,
            asset_name=str(asset.get("Description") or asset_code),
            preventive_due_at=preventive_due_at,
            open_corrective_count=sum(not work_order.closed for work_order in work_orders),
            corrective_work_orders=work_orders,
        )
