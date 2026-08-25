#!/usr/bin/env python3
"""Bounded live proof for the CFI Phase 3 Runtime and MCP boundary.

This is an operator probe, not a test suite. It creates one explicitly named
live-probe CFI row (retained for audit because production MCP access has no
delete operation), invokes the separate CFI Agent Runtime, and independently
reads the durable result through the private department MCP.

The verifier fails closed unless all of these are true:
* the CFI Runtime has an org-scoped Agent Identity and the configured Gateway;
* the CFI Runtime is present in Agent Registry;
* the CFI Gateway policy exposes exactly the Phase 3 CFI tools;
* the department MCP is explicitly on the Cloud SQL sibling backend;
* sibling scope expansion returns same-tenant Cloud SQL rows;
* a cross-tenant sibling request is rejected;
* the Runtime completes and persists evidence/outcome through MCP;
* Calendar site review and the deterministic CFI report are filed successfully.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests

PROJECT = os.environ.get("PROJECT_ID", "countersign-505107")
PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER", "777448403573")
LOCATION = os.environ.get("LOCATION", "us-central1")
ORG_ID = os.environ.get("ORG_ID", "334195573957")
GATEWAY = os.environ.get(
    "GATEWAY_RESOURCE",
    f"projects/{PROJECT}/locations/{LOCATION}/agentGateways/countersign-egress",
)
CFI_DISPLAY_NAME = "countersign-cfi"
CFI_POLICY = "countersign-mcp-cfi-workflow-tools"
MCP_URL = os.environ.get(
    "COUNTERSIGN_MCP_URL",
    f"https://countersign-department-mcp-{PROJECT_NUMBER}.{LOCATION}.run.app/mcp",
)
MCP_INVOKER_SERVICE_ACCOUNT = os.environ.get(
    "COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT",
    f"countersign-cfi-invoker@{PROJECT}.iam.gserviceaccount.com",
)
ROOT = Path(__file__).resolve().parents[2]
METADATA_PATH = ROOT / "cfi_deployment_metadata.json"
TENANT_ID = os.environ.get("CFI_PROBE_TENANT", "aurex")
BATCH_ID = os.environ.get("CFI_PROBE_BATCH", "AX-2291")


class VerificationError(RuntimeError):
    """The live CFI acceptance gate failed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def _gcloud(*args: str) -> Any:
    result = subprocess.run(
        ["gcloud", *args, f"--project={PROJECT}", "--format=json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout or "null")


def _runtime_resource() -> str:
    _require(METADATA_PATH.exists(), f"{METADATA_PATH} is missing")
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    resource = metadata.get("cfi_agent_runtime_id", "")
    _require(
        resource.startswith(f"projects/{PROJECT_NUMBER}/locations/{LOCATION}/reasoningEngines/"),
        f"invalid CFI Runtime resource in metadata: {resource!r}",
    )
    return resource


def _access_token() -> str:
    return subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _runtime_url(resource: str, method: str) -> str:
    return (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/"
        f"{resource}:{method}"
    )


def _verify_runtime_and_registry(resource: str) -> dict[str, Any]:
    token = _access_token()
    response = requests.get(
        _runtime_url(resource, "get").replace(":get", ""),
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    _require(response.ok, f"CFI Runtime GET returned HTTP {response.status_code}: {response.text[:300]}")
    payload = response.json()
    spec = payload.get("spec") or {}
    identity = spec.get("effectiveIdentity") or ""
    deployment = spec.get("deploymentSpec") or {}
    configured_gateway = (
        (deployment.get("agentGatewayConfig") or {})
        .get("agentToAnywhereConfig", {})
        .get("agentGateway")
    )
    _require(
        identity.startswith(f"agents.global.org-{ORG_ID}") and "@" not in identity,
        f"CFI Runtime is not using an org-scoped Agent Identity: {identity!r}",
    )
    _require(
        configured_gateway == GATEWAY,
        f"CFI Runtime Gateway mismatch: {configured_gateway!r} != {GATEWAY!r}",
    )

    agents = _gcloud("alpha", "agent-registry", "agents", "list", f"--location={LOCATION}")
    runtime_id = resource.rsplit("/", 1)[-1]
    matching = [
        agent
        for agent in agents
        if agent.get("displayName") == CFI_DISPLAY_NAME
        and runtime_id in str(agent.get("agentId"))
    ]
    _require(len(matching) == 1, f"expected one Registry CFI agent, found {len(matching)}")

    policy = _gcloud(
        "network-security",
        "authz-policies",
        "describe",
        CFI_POLICY,
        f"--location={LOCATION}",
    )
    _require(policy.get("action") == "ALLOW", "CFI Gateway policy is not ALLOW")
    policy_target = json.dumps(policy.get("target"))
    gateway_target_aliases = {
        GATEWAY,
        GATEWAY.replace(f"projects/{PROJECT}/", f"projects/{PROJECT_NUMBER}/"),
    }
    _require(
        any(target in policy_target for target in gateway_target_aliases),
        "CFI Gateway policy targets the wrong resource",
    )
    allowed_tools: set[str] = set()
    for rule in policy.get("httpRules") or []:
        for operation in (rule.get("to") or {}).get("operations") or []:
            for method in ((operation.get("mcp") or {}).get("methods") or []):
                params = method.get("params") or []
                allowed_tools.update(
                    param["exact"]
                    for param in params
                    if isinstance(param, dict) and "exact" in param
                )
    expected_tools = {
        "sibling_batch_query",
        "cfi_open",
        "cfi_get",
        "cfi_save",
        "cfi_trend_query",
        "chat_send_question",
        "chat_read_replies",
        "cfi_calendar_site_review",
        "cfi_report_write",
    }
    _require(
        allowed_tools == expected_tools,
        f"CFI policy tools mismatch: {sorted(allowed_tools)}",
    )
    return {
        "runtime_resource": resource,
        "effective_identity": identity,
        "agent_gateway": configured_gateway,
        "registry_agent": matching[0].get("name"),
        "policy": CFI_POLICY,
        "policy_tools": sorted(allowed_tools),
    }


def _verify_department_env() -> dict[str, Any]:
    service = _gcloud(
        "run",
        "services",
        "describe",
        "countersign-department-mcp",
        f"--region={LOCATION}",
    )
    containers = ((service.get("spec") or {}).get("template") or {}).get("spec") or {}
    env = {
        item.get("name"): item.get("value")
        for item in (containers.get("containers") or [{}])[0].get("env") or []
    }
    _require(
        env.get("COUNTERSIGN_SIBLING_BATCH_BACKEND") == "cloud_sql",
        "department MCP is not explicitly configured for the Cloud SQL sibling backend",
    )
    _require(
        env.get("COUNTERSIGN_SIBLING_BATCH_TABLE") == "batch_material_lots",
        "department MCP is not using batch_material_lots",
    )
    return {
        "service": "countersign-department-mcp",
        "url": service.get("status", {}).get("url"),
        "sibling_backend": env.get("COUNTERSIGN_SIBLING_BATCH_BACKEND"),
        "sibling_table": env.get("COUNTERSIGN_SIBLING_BATCH_TABLE"),
    }


def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    if "result" in payload and "cfi_id" not in payload and "tenant_id" not in payload:
        result = payload["result"]
        if isinstance(result, dict):
            return result
    return payload


async def _mcp_probe() -> dict[str, Any]:
    os.environ["COUNTERSIGN_MCP_URL"] = MCP_URL
    os.environ["COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT"] = MCP_INVOKER_SERVICE_ACCOUNT
    from app.domain.cfi_models import (
        CfiBudgetState,
        CfiInvestigation,
        CfiTriggerReason,
        SiblingBatchQueryResult,
    )
    from app.domain.department_tools import CFI_OPEN
    from app.domain.master_data import load_cfi_budget_spec
    from app.mcp.client import (
        call_cfi_tool,
        call_sibling_batch_tool,
    )

    budget = load_cfi_budget_spec()
    probe_suffix = uuid.uuid4().hex[:10]
    seed = CfiInvestigation(
        tenant_id=TENANT_ID,
        source_case_id=f"live-cfi-probe-{probe_suffix}",
        batch_id=BATCH_ID,
        trigger_reasons=[CfiTriggerReason.NO_HYPOTHESES],
        trigger_spec_version="live-probe",
        trigger_spec_sha256="live-probe",
        budget=CfiBudgetState(
            budget_spec_version=budget.spec_version,
            budget_spec_sha256=budget.spec_sha256,
        ),
    )
    opened = _unwrap(
        await call_cfi_tool(
            CFI_OPEN,
            {
                "tenant_id": TENANT_ID,
                "case_tenant_id": TENANT_ID,
                "investigation": seed.model_dump(mode="json"),
            },
        )
        or {}
    )
    opened_record = CfiInvestigation.model_validate(opened)
    _require(opened_record.revision == 0, "new CFI did not open at revision 0")

    repeated = _unwrap(
        await call_cfi_tool(
            CFI_OPEN,
            {
                "tenant_id": TENANT_ID,
                "case_tenant_id": TENANT_ID,
                "investigation": seed.model_dump(mode="json"),
            },
        )
        or {}
    )
    repeated_record = CfiInvestigation.model_validate(repeated)
    _require(
        repeated_record.cfi_id == opened_record.cfi_id
        and repeated_record.revision == opened_record.revision,
        "cfi_open was not idempotent",
    )

    sibling_payload = await call_sibling_batch_tool(
        {
            "tenant_id": TENANT_ID,
            "batch_id": BATCH_ID,
            "shared_attribute": "material_lot",
            "case_tenant_id": TENANT_ID,
        }
    )
    siblings = SiblingBatchQueryResult.model_validate(_unwrap(sibling_payload))
    sibling_ids = {candidate.batch_id for candidate in siblings.candidates}
    _require(
        sibling_ids >= {"AX-2292", "AX-2293"},
        f"Cloud SQL sibling query did not return expected same-lot rows: {sorted(sibling_ids)}",
    )
    _require(
        all(candidate.batch_id != BATCH_ID for candidate in siblings.candidates),
        "sibling query returned the queried batch itself",
    )
    _require(
        all(candidate.shared_attribute == "material_lot" for candidate in siblings.candidates),
        "sibling query returned an unexpected shared attribute",
    )

    cross_tenant_rejected = False
    try:
        await call_sibling_batch_tool(
            {
                "tenant_id": "helix",
                "batch_id": BATCH_ID,
                "shared_attribute": "material_lot",
                "case_tenant_id": TENANT_ID,
            }
        )
    except Exception:
        cross_tenant_rejected = True
    _require(cross_tenant_rejected, "cross-tenant sibling query was not rejected")

    return {
        "cfi_id": opened_record.cfi_id,
        "source_case_id": opened_record.source_case_id,
        "tenant_id": TENANT_ID,
        "batch_id": BATCH_ID,
        "siblings": sorted(sibling_ids),
        "cross_tenant_rejected": cross_tenant_rejected,
    }


def _invoke_runtime(resource: str, cfi_id: str) -> dict[str, Any]:
    token = _access_token()
    message = json.dumps({"cfi_id": cfi_id, "tenant_id": TENANT_ID})
    response = requests.post(
        _runtime_url(resource, "streamQuery") + "?alt=sse",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "class_method": "async_stream_query",
            "input": {
                "user_id": f"cfi-live-probe-{uuid.uuid4().hex[:8]}",
                "message": {"role": "user", "parts": [{"text": message}]},
            },
        },
        stream=True,
        timeout=(30, 360),
    )
    _require(
        response.status_code < 400,
        f"CFI Runtime streamQuery returned HTTP {response.status_code}: {response.text[:500]}",
    )
    invocation_id: str | None = None
    event_count = 0
    errors: list[str] = []
    try:
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line[5:].strip() if raw_line.startswith("data:") else raw_line.strip()
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_count += 1
            invocation_id = event.get("invocationId") or event.get("invocation_id") or invocation_id
            if event.get("errorCode") or event.get("errorMessage"):
                errors.append(json.dumps(event))
    finally:
        response.close()
    _require(event_count > 0, "CFI Runtime streamQuery returned no events")
    _require(not errors, f"CFI Runtime emitted errors: {errors[:2]}")
    return {"invocation_id": invocation_id, "event_count": event_count}


async def _wait_for_durable_close(cfi_id: str, timeout_seconds: int) -> dict[str, Any]:
    os.environ["COUNTERSIGN_MCP_URL"] = MCP_URL
    os.environ["COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT"] = MCP_INVOKER_SERVICE_ACCOUNT
    from app.domain.cfi_models import CfiInvestigation
    from app.domain.department_tools import CFI_GET
    from app.mcp.client import call_cfi_tool

    deadline = time.monotonic() + timeout_seconds
    last: CfiInvestigation | None = None
    while time.monotonic() < deadline:
        payload = _unwrap(
            await call_cfi_tool(
                CFI_GET,
                {
                    "tenant_id": TENANT_ID,
                    "case_tenant_id": TENANT_ID,
                    "cfi_id": cfi_id,
                },
            )
            or {}
        )
        if payload:
            last = CfiInvestigation.model_validate(payload)
            if last.outcome is not None:
                _require(last.revision > 0, "closed CFI was never durably revised")
                _require(last.hypotheses, "closed CFI has no hypothesis ledger")
                _require(last.evidence, "closed CFI has no captured evidence")
                _require(
                    any(item.source_tool == "sibling_batch_query" for item in last.evidence),
                    "closed CFI has no sibling_batch_query evidence",
                )
                _require(last.site_review is not None, "CFI site review was not filed")
                _require(last.site_review_error is None, "CFI site review has a persisted error")
                _require(last.report is not None, "CFI report was not filed")
                _require(last.report_error is None, "CFI report has a persisted error")
                return {
                    "cfi_id": last.cfi_id,
                    "revision": last.revision,
                    "outcome": last.outcome.value,
                    "hypothesis_count": len(last.hypotheses),
                    "evidence_count": len(last.evidence),
                    "evidence_tools": sorted({item.source_tool for item in last.evidence}),
                    "site_review_event_id": last.site_review.event_id,
                    "report_document_id": last.report.document_id,
                }
        await asyncio.sleep(5)
    raise VerificationError(
        f"CFI did not close durably within {timeout_seconds}s; "
        f"last_revision={getattr(last, 'revision', None)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("CFI_PROBE_TIMEOUT_SECONDS", "360")),
    )
    args = parser.parse_args()
    try:
        resource = _runtime_resource()
        runtime = _verify_runtime_and_registry(resource)
        department = _verify_department_env()
        mcp = asyncio.run(_mcp_probe())
        invocation = _invoke_runtime(resource, mcp["cfi_id"])
        durable = asyncio.run(_wait_for_durable_close(mcp["cfi_id"], args.timeout_seconds))
        print(
            json.dumps(
                {
                    "status": "ok",
                    "project": PROJECT,
                    "location": LOCATION,
                    "runtime": runtime,
                    "department_mcp": department,
                    "mcp_boundary": mcp,
                    "runtime_invocation": invocation,
                    "durable_cfi": durable,
                    "probe_record_retained": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
