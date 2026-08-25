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

"""``POST /scheduler/sterility-tick`` — complete due holds, wake parked cases.

Runs on a Cloud Scheduler cadence. Each call claims every incubation whose
``due_at`` has passed (the store update is the claim, so overlapping ticks
cannot complete the same row twice), then delivers a tick to the invocation
that checkpointed on that hold.

Completing an incubation is a lab-clock fact, not a batch disposition: the
workflow still has to pass the Annex 16 checklist and the QP still signs.
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from app.domain.cfi_interview_models import (
    CfiInterviewStatus,
    CfiInterviewTickResult,
)
from app.domain.cfi_launch_models import CfiLaunchTickResult
from app.domain.cfi_models import CfiLaunchStatus
from app.domain.department_tools import (
    CFI_INTERVIEW_TICK,
    CFI_LAUNCH_RESULT,
    CFI_START_TICK,
)
from app.domain.sterility_models import SterilityIncubationRecord
from app.domain.sterility_store import get_sterility_store
from app.mcp.client import (
    DepartmentToolCallError,
    call_cfi_scheduler_tool,
)
from app.scheduler.auth import verify_scheduler_token
from app.scheduler.resume import (
    ResumeError,
    build_cfi_resume_client,
    build_cfi_start_client,
    build_cfi_tick_message,
    build_resume_client,
    build_tick_message,
)

logger = logging.getLogger(__name__)

TICK_SOURCE = "cloud-scheduler"
CFI_TICK_MAX_INTERVIEWS_ENV = "COUNTERSIGN_CFI_TICK_MAX_INTERVIEWS"
CFI_TICK_LEASE_SECONDS_ENV = "COUNTERSIGN_CFI_TICK_LEASE_SECONDS"
CFI_START_MAX_ENV = "COUNTERSIGN_CFI_START_MAX"
CFI_START_LEASE_SECONDS_ENV = "COUNTERSIGN_CFI_START_LEASE_SECONDS"
CFI_START_MAX_ATTEMPTS_ENV = "COUNTERSIGN_CFI_START_MAX_ATTEMPTS"


def _required_cfi_tick_int(name: str, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise RuntimeError(f"{name} must be configured for production CFI ticks.")
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value

router = APIRouter(
    prefix="/scheduler",
    dependencies=[Depends(verify_scheduler_token)],
)


async def _wake(record: SterilityIncubationRecord, runner: Any | None) -> dict[str, Any]:
    """Resume the invocation parked on this hold."""
    outcome: dict[str, Any] = {
        "tenant_id": record.tenant_id,
        "batch_id": record.batch_id,
        "case_id": record.case_id,
        "completed_at": (
            record.completed_at.isoformat() if record.completed_at else None
        ),
    }
    handle = record.resume_handle
    if handle is None:
        # The clock is complete and durable; the case simply has no recorded
        # checkpoint to continue. Surfaced rather than swallowed. Not
        # recoverable by retrying: no handle will appear on its own.
        outcome["resumed"] = False
        outcome["recoverable"] = False
        outcome["reason"] = "no_resume_handle"
        logger.warning(
            "Incubation %s/%s completed with no resume handle.",
            record.tenant_id,
            record.batch_id,
        )
        return outcome

    message = build_tick_message(
        tenant_id=record.tenant_id,
        batch_id=record.batch_id,
        interrupt_id=handle.interrupt_id,
        source=TICK_SOURCE,
    )
    try:
        await build_resume_client(runner).resume(handle, message)
    except ResumeError as exc:
        # The hold stays complete either way. A recoverable failure may
        # clear on a later tick or a manual replay; a non-recoverable one
        # (the handle's session/invocation no longer exists) needs an
        # operator, and is flagged distinctly so it isn't mistaken for a
        # transient blip that will resolve itself.
        outcome["resumed"] = False
        outcome["recoverable"] = exc.recoverable
        outcome["reason"] = f"resume_failed: {exc}"
        log = logger.warning if exc.recoverable else logger.error
        log("Resume failed for %s/%s: %s", record.tenant_id, record.batch_id, exc)
        return outcome
    except Exception as exc:
        outcome["resumed"] = False
        outcome["recoverable"] = True
        outcome["reason"] = f"resume_failed: {exc}"
        logger.exception(
            "Resume failed for %s/%s", record.tenant_id, record.batch_id
        )
        return outcome

    outcome["resumed"] = True
    outcome["invocation_id"] = handle.invocation_id
    return outcome


async def _start_pending_cfis() -> list[dict[str, Any]]:
    """Claim and start a bounded batch of pending CFI Runtime handoffs."""
    max_starts = _required_cfi_tick_int(
        CFI_START_MAX_ENV, minimum=1, maximum=50
    )
    lease_seconds = _required_cfi_tick_int(
        CFI_START_LEASE_SECONDS_ENV, minimum=30, maximum=3600
    )
    max_attempts = _required_cfi_tick_int(
        CFI_START_MAX_ATTEMPTS_ENV, minimum=1, maximum=10
    )
    try:
        payload = await call_cfi_scheduler_tool(
            CFI_START_TICK,
            {
                "now": datetime.datetime.now(datetime.UTC).isoformat(),
                "lease_seconds": lease_seconds,
                "max_starts": max_starts,
                "max_attempts": max_attempts,
            },
        )
    except DepartmentToolCallError as exc:
        logger.error("CFI launch claim failed: %s", exc)
        raise HTTPException(
            status_code=503, detail="CFI launch control plane unavailable."
        ) from exc
    if not payload:
        raise HTTPException(
            status_code=502, detail="CFI launch control plane returned no result."
        )
    body: object = payload
    if isinstance(body, dict) and "result" in body and "items" not in body:
        body = body["result"]
    try:
        tick = CfiLaunchTickResult.model_validate(body)
    except ValueError as exc:
        logger.error("Invalid CFI launch tick result: %s", exc)
        raise HTTPException(
            status_code=502, detail="Invalid CFI launch control-plane result."
        ) from exc

    client = None
    records: list[dict[str, Any]] = []
    for item in tick.items:
        result: dict[str, Any] = {
            "cfi_id": item.cfi_id,
            "tenant_id": item.tenant_id,
            "source_case_id": item.source_case_id,
            "batch_id": item.batch_id,
            "launch_attempts": item.launch_attempts,
            "started": False,
        }
        try:
            if client is None:
                client = build_cfi_start_client()
            invocation_id = await client.start(
                cfi_id=item.cfi_id, tenant_id=item.tenant_id
            )
        except ResumeError as exc:
            error = f"{exc}; recoverable={exc.recoverable}"
            result["reason"] = f"launch_failed: {error}"
            result["recoverable"] = exc.recoverable
            logger.warning("CFI launch failed for %s: %s", item.cfi_id, error)
            launch_status = CfiLaunchStatus.FAILED.value
            invocation_id = None
            launch_error = error
        except Exception as exc:
            launch_error = str(exc)
            result["reason"] = f"launch_failed: {launch_error}"
            result["recoverable"] = True
            logger.exception("CFI launch failed for %s", item.cfi_id)
            launch_status = CfiLaunchStatus.FAILED.value
            invocation_id = None
        else:
            launch_status = CfiLaunchStatus.STARTED.value
            launch_error = None
            result["started"] = True
            result["invocation_id"] = invocation_id
            result["reason"] = "started"

        try:
            await call_cfi_scheduler_tool(
                CFI_LAUNCH_RESULT,
                {
                    "tenant_id": item.tenant_id,
                    "case_tenant_id": item.tenant_id,
                    "cfi_id": item.cfi_id,
                    "launch_status": launch_status,
                    "runtime_invocation_id": invocation_id,
                    "launch_error": launch_error,
                },
            )
        except DepartmentToolCallError as exc:
            result["recorded"] = False
            result["recording_error"] = str(exc)
            logger.error("Could not record CFI launch result for %s: %s", item.cfi_id, exc)
        else:
            result["recorded"] = True
        records.append(result)
    return records


@router.post("/cfi-tick")
async def cfi_tick(request: Request) -> dict[str, Any]:
    """Poll Chat interviews inside the department MCP and wake CFI Runtime.

    The FastAPI scheduler never opens Cloud SQL and never receives human
    reply text. The MCP tick claims/reads/nudges and returns only interview
    status plus a persisted resume handle. A resume failure leaves the
    interview in a retryable captured/unanswered state for a later tick.
    """
    launch_records = await _start_pending_cfis()
    max_interviews = _required_cfi_tick_int(
        CFI_TICK_MAX_INTERVIEWS_ENV, minimum=1, maximum=100
    )
    lease_seconds = _required_cfi_tick_int(
        CFI_TICK_LEASE_SECONDS_ENV, minimum=1, maximum=3600
    )
    try:
        payload = await call_cfi_scheduler_tool(
            CFI_INTERVIEW_TICK,
            {
                "now": datetime.datetime.now(datetime.UTC).isoformat(),
                "lease_seconds": lease_seconds,
                "max_interviews": max_interviews,
            },
        )
    except DepartmentToolCallError as exc:
        logger.error("CFI interview MCP tick failed: %s", exc)
        raise HTTPException(
            status_code=503, detail="CFI interview control plane unavailable."
        ) from exc
    if not payload:
        raise HTTPException(
            status_code=502, detail="CFI interview MCP returned no tick result."
        )
    body: object = payload
    if isinstance(body, dict) and "result" in body and "items" not in body:
        body = body["result"]
    try:
        tick = CfiInterviewTickResult.model_validate(body)
    except ValueError as exc:
        logger.error("Invalid CFI interview MCP tick result: %s", exc)
        raise HTTPException(
            status_code=502, detail="Invalid CFI interview control-plane result."
        ) from exc

    runner = getattr(request.app.state, "cfi_runner", None)
    resume_client = None
    records: list[dict[str, Any]] = []
    for item in tick.items:
        result: dict[str, Any] = {
            "tenant_id": item.tenant_id,
            "interview_id": item.interview_id,
            "status": item.status.value,
            "nudge_sent": item.nudge_sent,
            "nudge_count": item.nudge_count,
            "reason": item.reason,
            "resumed": False,
        }
        if item.status not in {
            CfiInterviewStatus.REPLY_CAPTURED,
            CfiInterviewStatus.INTERVIEW_UNANSWERED,
        }:
            records.append(result)
            continue
        handle = item.resume_handle
        if handle is None:
            result["reason"] = "resume_required_without_resume_handle"
            result["recoverable"] = False
            logger.error(
                "CFI interview %s/%s requires resume but has no handle.",
                item.tenant_id,
                item.interview_id,
            )
            records.append(result)
            continue
        if resume_client is None:
            try:
                resume_client = build_cfi_resume_client(runner)
            except Exception as exc:
                logger.error("CFI resume transport is unavailable: %s", exc)
                raise HTTPException(
                    status_code=503, detail="CFI resume transport unavailable."
                ) from exc
        message = build_cfi_tick_message(
            tenant_id=item.tenant_id,
            interview_id=item.interview_id,
            interrupt_id=handle.interrupt_id,
            source=TICK_SOURCE,
        )
        try:
            await resume_client.resume(handle, message)
        except ResumeError as exc:
            result["recoverable"] = exc.recoverable
            result["reason"] = f"resume_failed: {exc}"
            log = logger.warning if exc.recoverable else logger.error
            log(
                "CFI resume failed for %s/%s: %s",
                item.tenant_id,
                item.interview_id,
                exc,
            )
        except Exception as exc:
            result["recoverable"] = True
            result["reason"] = f"resume_failed: {exc}"
            logger.exception(
                "CFI resume failed for %s/%s", item.tenant_id, item.interview_id
            )
        else:
            result["resumed"] = True
            result["invocation_id"] = handle.invocation_id
        records.append(result)

    return {
        "launch_claimed": len(launch_records),
        "launch_started": sum(1 for item in launch_records if item.get("started")),
        "launch_records": launch_records,
        "claimed": len(tick.items),
        "nudged": sum(1 for item in tick.items if item.nudge_sent),
        "resume_required": sum(
            1
            for item in tick.items
            if item.status
            in {
                CfiInterviewStatus.REPLY_CAPTURED,
                CfiInterviewStatus.INTERVIEW_UNANSWERED,
            }
        ),
        "resumed": sum(1 for item in records if item.get("resumed")),
        "records": records,
        "decision_authority": "none",
    }


@router.post("/sterility-tick")
async def sterility_tick(request: Request) -> dict[str, Any]:
    completed = get_sterility_store().tick_due()
    runner = getattr(request.app.state, "runner", None)
    results = [await _wake(record, runner) for record in completed]
    return {
        "completed": len(results),
        "resumed": sum(1 for item in results if item.get("resumed")),
        "records": results,
        "decision_authority": "none",
    }


def attach_scheduler_routes(app: FastAPI) -> None:
    app.include_router(router)
