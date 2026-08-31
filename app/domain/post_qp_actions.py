# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Isolated post-QP maintenance and sponsor-notification requests."""

from __future__ import annotations

import hashlib
from typing import Literal

from google.adk.agents.context import Context
from google.adk.workflow import node

from app.domain.department_tools import (
    OPENMAINT_CORRECTIVE_REQUEST,
    SPONSOR_NOTIFICATION_REQUEST,
)
from app.domain.post_qp_models import (
    CorrectiveMaintenanceResult,
    PostQpActionError,
    PostQpActionResult,
    SponsorNotificationRequest,
    SponsorNotificationResult,
)
from app.domain.qp_models import QpReviewedPackage
from app.mcp.client import (
    call_post_qp_maintenance_tool,
    call_post_qp_sponsor_tool,
)

POST_QP_RESULT_STATE_KEY = "post_qp_action_result"


def idempotency_key(tenant_id: str, case_id: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}\0{case_id}".encode()).hexdigest()
    return f"countersign-{digest}"


def _action_error(
    action: Literal["maintenance", "sponsor_notification"], exc: Exception
) -> PostQpActionError:
    return PostQpActionError(
        action=action,
        error_type=type(exc).__name__[:128],
        message=(str(exc) or "Unspecified post-QP action failure.")[:1024],
    )


async def execute_post_qp_actions(
    ctx: Context, node_input: QpReviewedPackage
) -> QpReviewedPackage:
    reviewed = (
        node_input
        if isinstance(node_input, QpReviewedPackage)
        else QpReviewedPackage.model_validate(node_input)
    )
    classified = reviewed.release_package.investigation.case.classified
    key = idempotency_key(classified.tenant_id, classified.case_id)
    summary = (
        f"Corrective maintenance requested for deviation {classified.case_id}, "
        f"batch {classified.batch_id}, after QP outcome "
        f"{reviewed.qp_confirmation.outcome.value}. decision_authority: none"
    )
    maintenance = None
    sponsor = None
    maintenance_error = None
    sponsor_error = None
    try:
        raw = await call_post_qp_maintenance_tool(
            OPENMAINT_CORRECTIVE_REQUEST,
            {
                "tenant_id": classified.tenant_id,
                "case_id": classified.case_id,
                "batch_id": classified.batch_id,
                "summary": summary,
                "idempotency_key": key,
                "lifecycle_state": "qp_reviewed",
                "case_tenant_id": classified.tenant_id,
            },
        )
        maintenance = CorrectiveMaintenanceResult.model_validate(raw)
    except Exception as exc:
        maintenance_error = _action_error("maintenance", exc)
    try:
        sponsor_request = SponsorNotificationRequest(
            tenant_id=classified.tenant_id,
            case_id=classified.case_id,
            batch_id=classified.batch_id,
            qp_outcome=reviewed.qp_confirmation.outcome.value,
            qp_recorded_at=reviewed.qp_confirmation.recorded_at,
            idempotency_key=key,
            lifecycle_state="qp_reviewed",
        )
        raw = await call_post_qp_sponsor_tool(
            SPONSOR_NOTIFICATION_REQUEST,
            sponsor_request.model_dump(mode="json")
            | {"case_tenant_id": classified.tenant_id},
        )
        sponsor = SponsorNotificationResult.model_validate(raw)
    except Exception as exc:
        sponsor_error = _action_error("sponsor_notification", exc)
    ctx.state[POST_QP_RESULT_STATE_KEY] = PostQpActionResult(
        maintenance=maintenance,
        sponsor_notification=sponsor,
        maintenance_error=maintenance_error,
        sponsor_notification_error=sponsor_error,
    ).model_dump(mode="json")
    return reviewed


request_post_qp_actions = node(
    name="request_post_qp_actions", rerun_on_resume=True
)(execute_post_qp_actions)
