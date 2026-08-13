#!/usr/bin/env python3
"""Deploy the Countersign deviation-case Workflow to Agent Runtime.

Separate from the hello agent, which stays a narrow Gateway deny probe. This
deploys the real graph (``app.agent``).

An Agent Identity SPIFFE principal cannot be a Cloud SQL IAM database user —
confirmed against the live instance (``gcloud sql users create
<effective_identity>`` rejects the name: too long, and the type only accepts
user accounts, service accounts, or groups). So this Runtime deploy:

* uses Vertex-managed sessions (``COUNTERSIGN_ALLOW_VERTEX_SESSIONS=1``)
* reaches LIMS/EM/CoA *and* the sterility clock only through
  ``COUNTERSIGN_MCP_URL`` — the department MCP service, which runs with a
  real service-account credential and already holds Cloud SQL access

Both ``identity_type=AGENT_IDENTITY`` and ``agent_gateway_config`` are set;
omitting either leaves the agent quietly ungoverned.

Packaging rule (avoids ModuleNotFoundError on unpickle): copy the package into
a staging dir, chdir there, then import.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
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

# The graph's runtime dependencies. The Agent Identity Runtime never opens
# Cloud SQL itself (see COUNTERSIGN_ALLOW_VERTEX_SESSIONS above), so this
# does not need sqlalchemy/asyncpg/cloud-sql-python-connector/pg8000 — those
# stay on the department MCP service, which holds a real credential.
CASE_REQUIREMENTS = [
    "google-cloud-aiplatform[adk,agent_engines]",
    "google-adk[gcp,otel-gcp]>=2.6.3,<3.0.0",
    "mcp>=1.26.0,<2.0.0",
    "cloudpickle",
    "pydantic",
]

DESCRIPTION = (
    "Countersign deviation-case workflow: evidence assembly, Annex 16 "
    "confirmation checklist and QP sign-off. No agent holds decision authority."
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="countersign-505107")
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--display-name", default="countersign-case")
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
        help="https Streamable HTTP URL of the department MCP service.",
    )
    parser.add_argument(
        "--sterility-hold-seconds",
        default=None,
        help=(
            "Compress the 14-day incubation for a demo run. Records are "
            "labelled compressed_for_demo when this is set."
        ),
    )
    args = parser.parse_args()

    if not args.mcp_url.startswith("https://"):
        raise SystemExit("--mcp-url must be https so egress crosses the gateway.")

    staging_bucket = args.staging_bucket or f"gs://{args.project}-agent-staging"
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
        # Gemini 3.5+ is served from the global Vertex endpoint; the Runtime
        # region stays args.region.
        "GOOGLE_CLOUD_LOCATION": "global",
        "MODEL_NAME": args.model,
        # Agent Identity cannot hold Cloud SQL IAM credentials, so this
        # deploy does not set INSTANCE_CONNECTION_NAME/DB_IAM_USER. Sessions
        # use Vertex-managed storage; services.py otherwise fails closed to
        # prevent silently hiding SQL rows that were never the plan here.
        # GOOGLE_CLOUD_AGENT_ENGINE_LOCATION is set explicitly so session
        # storage stays pinned to the Runtime's actual region rather than
        # implicitly following GOOGLE_CLOUD_LOCATION=global (model routing).
        "COUNTERSIGN_ALLOW_VERTEX_SESSIONS": "1",
        "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION": args.region,
        "GOOGLE_CLOUD_PROJECT": args.project,
        # Department tools, and the sterility clock, must egress through
        # the department MCP service, never run in-process.
        "COUNTERSIGN_MCP_URL": args.mcp_url,
    }
    if args.sterility_hold_seconds:
        env_vars["COUNTERSIGN_STERILITY_HOLD_SECONDS"] = str(
            args.sterility_hold_seconds
        )

    staging_dir = tempfile.mkdtemp(prefix="countersign_case_deploy_")
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

        # Build-time env so the graph imports cleanly; the deployed values are
        # the env_vars above.
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
        os.environ["MODEL_NAME"] = args.model
        from app.agent import app as adk_app

        runtime_app = AdkApp(app=adk_app, enable_tracing=True)

        deploy_config: dict = {
            "staging_bucket": staging_bucket,
            "requirements": CASE_REQUIREMENTS,
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
            print(f"Updating existing {existing_name}")
            deployed = client.agent_engines.update(
                name=existing_name, agent=runtime_app, config=deploy_config
            )
        else:
            print("Step 1: create AGENT_IDENTITY shell")
            shell = client.agent_engines.create(
                config={
                    "display_name": args.display_name,
                    "description": DESCRIPTION,
                    "identity_type": types.IdentityType.AGENT_IDENTITY,
                }
            )
            effective = assert_identity(shell.api_resource.spec.effective_identity)
            principal = f"principal://{effective}"
            print("Step 2: grant project IAM + endpoint egress")
            grant_project_roles(args.project, principal, BASELINE_ROLES)
            grant_endpoint_egress(args.project, args.region, principal)
            print("Step 3: update with the case AdkApp + agent_gateway_config")
            deployed = client.agent_engines.update(
                name=shell.api_resource.name,
                agent=runtime_app,
                config=deploy_config,
            )
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
        raise SystemExit("agentGatewayConfig missing after deploy")
    print(f"agentGatewayConfig OK: {gateway}")

    principal = f"principal://{effective}"
    grant_project_roles(args.project, principal, BASELINE_ROLES)
    grant_endpoint_egress(args.project, args.region, principal)

    meta = {
        "case_agent_runtime_id": deployed.api_resource.name,
        "deployment_target": "agent_runtime",
        "deploy_mode": "sdk_adkapp_source",
        "agent_directory": "app",
        "deployment_timestamp": datetime.now(UTC).isoformat(),
        "effective_identity": effective,
        "agent_gateway": args.agent_gateway,
        "sessions": "vertex_managed",
        "mcp_url": args.mcp_url,
        "project_number": project_number(args.project),
    }
    (ROOT / "case_deployment_metadata.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(meta, indent=2))
    print(
        "\nNext: export COUNTERSIGN_CASE_ENGINE="
        f"{deployed.api_resource.name} on the tick service so a completed "
        "hold resumes this deployment."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
