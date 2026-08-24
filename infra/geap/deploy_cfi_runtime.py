#!/usr/bin/env python3
"""Deploy the separate Phase 1 CFI Workflow to Agent Runtime.

The CFI graph is deliberately not embedded in the proven deviation-case
Runtime. It is deployed from ``app.cfi_app`` with its own Agent Identity and
its own Gateway MCP allow-list.

The Runtime never opens Cloud SQL. It reaches cfi_investigation and the
sibling-batch source through the private department MCP, using a dedicated
OIDC-invoker service account that the CFI Agent Identity may impersonate.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runtime_identity import (
    BASELINE_ROLES,
    assert_identity,
    ensure_bucket,
    grant_endpoint_egress,
    grant_project_roles,
    project_number,
)

ROOT = Path(__file__).resolve().parents[2]

CFI_REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent_engines]",
    "google-adk[gcp,otel-gcp]>=2.6.3,<3.0.0",
    "mcp>=1.26.0,<2.0.0",
    "cloudpickle",
    "pydantic",
    "google-auth>=2.38.0,<3.0.0",
    "requests>=2.32.0,<3.0.0",
]

DESCRIPTION = (
    "Countersign Phase 1 Cross-Functional Investigation workflow: bounded "
    "hypothesis planning, tenant-locked sibling scope expansion, and durable "
    "evidence ledger. No agent holds decision authority."
)


def _run(command: list[str]) -> str:
    return subprocess.run(
        command, check=True, capture_output=True, text=True
    ).stdout.strip()


def _ensure_service_account(project: str, email: str) -> None:
    described = subprocess.run(
        ["gcloud", "iam", "service-accounts", "describe", email, f"--project={project}"],
        capture_output=True,
        text=True,
    )
    if described.returncode == 0:
        return
    account_id = email.split("@", 1)[0]
    subprocess.run(
        [
            "gcloud",
            "iam",
            "service-accounts",
            "create",
            account_id,
            f"--project={project}",
            "--display-name=Countersign CFI MCP invoker",
        ],
        check=True,
    )


def _grant_cfi_mcp_access(
    *, project: str, region: str, service: str, invoker_sa: str, principal: str
) -> None:
    # The OIDC token presented to Cloud Run is minted as invoker_sa. The
    # Agent Identity also receives run.invoker so the boundary is explicit in
    # IAM and in the runtime's own audited principal path.
    for member in (f"serviceAccount:{invoker_sa}", principal):
        subprocess.run(
            [
                "gcloud",
                "run",
                "services",
                "add-iam-policy-binding",
                service,
                f"--project={project}",
                f"--region={region}",
                f"--member={member}",
                "--role=roles/run.invoker",
                "--condition=None",
                "--quiet",
            ],
            check=True,
        )

    subprocess.run(
        [
            "gcloud",
            "iam",
            "service-accounts",
            "add-iam-policy-binding",
            invoker_sa,
            f"--project={project}",
            f"--member={principal}",
            "--role=roles/iam.serviceAccountTokenCreator",
            "--condition=None",
            "--quiet",
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="countersign-505107")
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--display-name", default="countersign-cfi")
    parser.add_argument(
        "--agent-gateway",
        default=(
            "projects/countersign-505107/locations/us-central1/"
            "agentGateways/countersign-egress"
        ),
    )
    parser.add_argument("--staging-bucket", default=None)
    parser.add_argument("--model", default="gemini-3.6-flash")
    parser.add_argument(
        "--mcp-url",
        required=True,
        help="https Streamable HTTP URL of the private department MCP service.",
    )
    parser.add_argument(
        "--mcp-invoker-service-account",
        default=None,
        help=(
            "Dedicated service account used to mint the Cloud Run OIDC token. "
            "Defaults to countersign-cfi-invoker@PROJECT.iam.gserviceaccount.com."
        ),
    )
    args = parser.parse_args()

    if not args.mcp_url.startswith("https://"):
        raise SystemExit("--mcp-url must be https so egress crosses the gateway.")
    invoker_sa = args.mcp_invoker_service_account or (
        f"countersign-cfi-invoker@{args.project}.iam.gserviceaccount.com"
    )
    service = "countersign-department-mcp"
    staging_bucket = args.staging_bucket or f"gs://{args.project}-agent-staging"

    _ensure_service_account(args.project, invoker_sa)
    ensure_bucket(args.project, args.region, staging_bucket)

    import vertexai
    from vertexai import types
    from vertexai.agent_engines import AdkApp

    client = vertexai.Client(
        project=args.project,
        location=args.region,
        http_options={"api_version": "v1beta1"},
    )
    vertexai.init(
        project=args.project,
        location=args.region,
        staging_bucket=staging_bucket,
    )

    existing_name = None
    for engine in client.agent_engines.list():
        if engine.api_resource.display_name == args.display_name:
            existing_name = engine.api_resource.name
            break

    env_vars = {
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
        "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES": "false",
        "GOOGLE_CLOUD_LOCATION": "global",
        "MODEL_NAME": args.model,
        "COUNTERSIGN_ALLOW_VERTEX_SESSIONS": "1",
        "COUNTERSIGN_MCP_URL": args.mcp_url,
        "COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT": invoker_sa,
    }

    staging_dir = tempfile.mkdtemp(prefix="countersign_cfi_deploy_")
    original_cwd = os.getcwd()
    try:
        shutil.copytree(
            ROOT / "app",
            Path(staging_dir) / "app",
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".pytest_cache", "*.sqlite"
            ),
        )
        os.chdir(staging_dir)
        sys.path = [staging_dir] + [
            path for path in sys.path if path not in (str(ROOT), "")
        ]
        for module in list(sys.modules):
            if module == "app" or module.startswith("app."):
                del sys.modules[module]

        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
        os.environ["MODEL_NAME"] = args.model
        from app.agent import cfi_app

        runtime_app = AdkApp(app=cfi_app, enable_tracing=True)
        deploy_config: dict = {
            "staging_bucket": staging_bucket,
            "python_version": "3.12",
            "requirements": CFI_REQUIREMENTS,
            "extra_packages": ["app"],
            "env_vars": env_vars,
            "display_name": args.display_name,
            "description": DESCRIPTION,
            "identity_type": types.IdentityType.AGENT_IDENTITY,
            "agent_gateway_config": {
                "agent_to_anywhere_config": {"agent_gateway": args.agent_gateway}
            },
            "min_instances": 1,
            "max_instances": 2,
            "resource_limits": {"cpu": "2", "memory": "8Gi"},
        }

        if existing_name:
            print(f"Deleting existing {existing_name} to apply the CFI package")
            client.agent_engines.delete(name=existing_name, force=True)

        print("Step 1: create AGENT_IDENTITY shell + cfi_app in one call")
        deployed = client.agent_engines.create(agent=runtime_app, config=deploy_config)
        effective = assert_identity(deployed.api_resource.spec.effective_identity)
        principal = f"principal://{effective}"
        print("Step 2: grant project IAM + registered endpoint egress")
        grant_project_roles(args.project, principal, BASELINE_ROLES)
        grant_endpoint_egress(args.project, args.region, principal)
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(staging_dir, ignore_errors=True)

    deployed = client.agent_engines.get(name=deployed.api_resource.name)
    spec = deployed.api_resource.spec
    effective = assert_identity(spec.effective_identity)
    gateway = getattr(
        getattr(spec, "deployment_spec", None), "agent_gateway_config", None
    )
    if gateway is None:
        raise SystemExit("agentGatewayConfig missing after CFI deploy")
    print(f"agentGatewayConfig OK: {gateway}")

    principal = f"principal://{effective}"
    grant_project_roles(args.project, principal, BASELINE_ROLES)
    grant_endpoint_egress(args.project, args.region, principal)
    _grant_cfi_mcp_access(
        project=args.project,
        region=args.region,
        service=service,
        invoker_sa=invoker_sa,
        principal=principal,
    )

    meta = {
        "cfi_agent_runtime_id": deployed.api_resource.name,
        "deployment_target": "agent_runtime",
        "deploy_mode": "sdk_adkapp_source",
        "agent_directory": "app",
        "entrypoint": "app.cfi_app",
        "deployment_timestamp": datetime.now(UTC).isoformat(),
        "effective_identity": effective,
        "agent_gateway": args.agent_gateway,
        "sessions": "vertex_managed",
        "mcp_url": args.mcp_url,
        "mcp_invoker_service_account": invoker_sa,
        "project_number": project_number(args.project),
    }
    (ROOT / "cfi_deployment_metadata.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(meta, indent=2))
    print(
        "\nNext: import infra/geap/mcp-cfi-workflow-tools-allow.yaml and run "
        "infra/geap/verify_cfi_live.py."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
