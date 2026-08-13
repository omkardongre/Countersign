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

import logging
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request

from app.domain.sterility_models import SterilityIncubationRecord
from app.domain.sterility_store import get_sterility_store
from app.scheduler.auth import verify_scheduler_token
from app.scheduler.resume import ResumeError, build_resume_client, build_tick_message

logger = logging.getLogger(__name__)

TICK_SOURCE = "cloud-scheduler"

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
