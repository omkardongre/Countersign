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

"""``GET /fleet/manifest`` — the real agents in the deviation-case graph.

Read-only and unauthenticated by design: this reports the static shape of
``app/domain/case_graph.py`` (which agents exist, what each is allowed to
touch), not any case data. No tenant, batch, or finding appears here. This
is the endpoint the desk's fleet view and the demo film read from, per
``final_reseach/11_demo_and_proof_plan.md`` Beat 2b.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from app.domain.fleet_manifest import FleetAgentEntry, build_fleet_manifest
from app.domain.workflow_meta import CASE_WORKFLOW_VERSION

router = APIRouter(prefix="/fleet")


@router.get("/manifest")
def fleet_manifest() -> dict[str, object]:
    agents: list[FleetAgentEntry] = build_fleet_manifest()
    return {
        "workflow_version": CASE_WORKFLOW_VERSION,
        "agent_count": len(agents),
        "agents": [agent.model_dump(mode="json") for agent in agents],
        "decision_authority": "none",
    }


def attach_fleet_routes(app: FastAPI) -> None:
    app.include_router(router)
