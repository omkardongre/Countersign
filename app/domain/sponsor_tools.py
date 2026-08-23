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

"""Sponsor scope policy and the one tool the sponsor liaison may call.

Mirrors ``infra/geap/tqa_spike/src/countersign_tqa/policy.py``'s fail-closed
shape (explicit allow-list, named deny reasons, deny on any missing field)
for the cross-company boundary rather than the cross-department one: a
sponsor's own Quality Agreement with this CDMO binds them to exactly one
tenant, never the department-read tenant-equals-case-tenant check that
policy makes.

This is deliberately not routed through the department MCP's TQA
``ext_proc`` callout. That callout inspects ``lims_query``/``em_query``/
``coa_fetch`` tenant-vs-case-tenant tuples for the internal case Workflow's
own MCP calls; a sponsor is not a department and is not calling those tools,
so reusing that policy would either need to be silently widened to cover a
different question or would not apply at all. The scope check here answers
a narrower question — which tenant this sponsor is contractually allowed to
ask about at all — and is enforced in this module specifically so a
gateway-policy change elsewhere can never accidentally loosen it.
"""

from __future__ import annotations

import os

from app.domain.case_status_models import CaseStatus
from app.domain.case_status_store import get_case_status_store
from app.domain.sponsor_models import (
    SponsorBatchLifecycleView,
    SponsorBatchStatusQuery,
    SponsorBatchStatusResult,
    SponsorScopeDenied,
)

SPONSOR_TENANT_MAP_ENV = "COUNTERSIGN_SPONSOR_TENANT_MAP"

_STATUS_TO_VIEW = {
    CaseStatus.ON_STERILITY_HOLD: SponsorBatchLifecycleView.ON_STERILITY_HOLD,
    CaseStatus.QP_CONFIRMED: SponsorBatchLifecycleView.QP_CONFIRMED,
    CaseStatus.QP_DECLINED: SponsorBatchLifecycleView.QP_DECLINED,
    CaseStatus.QP_COULD_NOT_CONFIRM: SponsorBatchLifecycleView.QP_COULD_NOT_CONFIRM,
}


def _parse_sponsor_tenant_map(raw: str) -> dict[str, str]:
    """``sponsor_id:tenant_id,sponsor_id:tenant_id`` -> dict.

    Deployment configuration, not user input, but parsed strictly anyway —
    a malformed entry must fail closed at startup, not silently drop a
    sponsor's access or, worse, silently grant one.
    """
    mapping: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise RuntimeError(
                f"{SPONSOR_TENANT_MAP_ENV} entry {entry!r} is not "
                "sponsor_id:tenant_id."
            )
        sponsor_id, _, tenant_id = entry.partition(":")
        sponsor_id, tenant_id = sponsor_id.strip(), tenant_id.strip()
        if not sponsor_id or not tenant_id:
            raise RuntimeError(
                f"{SPONSOR_TENANT_MAP_ENV} entry {entry!r} has an empty "
                "sponsor_id or tenant_id."
            )
        if sponsor_id in mapping and mapping[sponsor_id] != tenant_id:
            raise RuntimeError(
                f"{SPONSOR_TENANT_MAP_ENV} lists {sponsor_id!r} for both "
                f"{mapping[sponsor_id]!r} and {tenant_id!r}. A sponsor "
                "identity must bind to exactly one tenant."
            )
        mapping[sponsor_id] = tenant_id
    return mapping


def sponsor_tenant_map() -> dict[str, str]:
    """The CDMO's own record of which sponsor may ask about which tenant.

    No fallback default map: an unset ``COUNTERSIGN_SPONSOR_TENANT_MAP``
    means no sponsor is authorized for anything, which is the only safe
    default for a cross-company read boundary. A CDMO onboarding a new
    sponsor adds one entry here; nothing about the graph, the agents, or
    the department tools changes.
    """
    raw = os.environ.get(SPONSOR_TENANT_MAP_ENV, "").strip()
    if not raw:
        return {}
    return _parse_sponsor_tenant_map(raw)


def authorize_sponsor_query(query: SponsorBatchStatusQuery) -> None:
    """Deny unless this sponsor's Quality Agreement covers this tenant.

    Raises rather than returning a bool so a caller cannot forget to check
    a return value — the same reasoning ``SterilityTenantMismatch`` and
    ``CaseMemoryTenantMismatch`` already use for their own cross-boundary
    checks (``department_tools.py``).
    """
    bound_tenant = sponsor_tenant_map().get(query.sponsor_id)
    if bound_tenant is None or bound_tenant != query.tenant_id:
        raise SponsorScopeDenied(
            f"Sponsor {query.sponsor_id!r} is not party to tenant "
            f"{query.tenant_id!r}'s Quality Agreement."
        )


def sponsor_batch_status(query: SponsorBatchStatusQuery) -> SponsorBatchStatusResult:
    """The one tool the sponsor liaison agent may call.

    Denies first, unconditionally, on scope. Only then reads
    ``case_status`` — a batch that does not exist and a batch the sponsor
    is not authorized to ask about must not be distinguishable by response
    latency or shape, so the deny path never touches the store.
    """
    authorize_sponsor_query(query)
    record = get_case_status_store().get(
        tenant_id=query.tenant_id, batch_id=query.batch_id
    )
    if record is None:
        return SponsorBatchStatusResult(
            tenant_id=query.tenant_id,
            batch_id=query.batch_id,
            lifecycle=SponsorBatchLifecycleView.NOT_FOUND,
            decision_authority="none",
        )
    return SponsorBatchStatusResult(
        tenant_id=record.tenant_id,
        batch_id=record.batch_id,
        lifecycle=_STATUS_TO_VIEW[record.status],
        sterility_hold_due_at=(
            record.sterility_due_at.isoformat() if record.sterility_due_at else None
        ),
        qp_confirmed_at=(
            record.qp_confirmed_at.isoformat() if record.qp_confirmed_at else None
        ),
        decision_authority="none",
    )
