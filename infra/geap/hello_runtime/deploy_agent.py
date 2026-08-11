#!/usr/bin/env python3
"""Deploy Countersign hello agent — official Agent Identity + Gateway path.

Follows:
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-identity
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-gateway-runtime-deploy
- GoogleCloudPlatform/cloud-networking-solutions demos/agent-gateway mortgage-agent/deploy_agent.py

Critical packaging rule (avoids ModuleNotFoundError on unpickle):
  copy agent package → chdir staging → THEN import + AdkApp + create/update.
  Never import the agent package from the repo path before staging.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELLO_DIR = Path(__file__).resolve().parent
ORG_ID = os.environ.get("ORG_ID", "334195573957")

BASELINE_ROLES = [
    "roles/aiplatform.user",
    "roles/aiplatform.expressUser",
    "roles/aiplatform.agentDefaultAccess",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/browser",
    "roles/cloudapiregistry.viewer",
    "roles/agentregistry.viewer",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
]


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def _ensure_bucket(project: str, location: str, bucket: str) -> None:
    name = bucket.removeprefix("gs://")
    listed = subprocess.run(
        ["gcloud", "storage", "buckets", "describe", f"gs://{name}", f"--project={project}"],
        capture_output=True,
        text=True,
    )
    if listed.returncode == 0:
        return
    subprocess.run(
        [
            "gcloud",
            "storage",
            "buckets",
            "create",
            f"gs://{name}",
            f"--project={project}",
            f"--location={location}",
            "--uniform-bucket-level-access",
        ],
        check=True,
    )


def _project_number(project: str) -> str:
    return _run(
        ["gcloud", "projects", "describe", project, "--format=value(projectNumber)"]
    )


def _grant_project_roles(project: str, principal: str) -> None:
    from google.cloud import resourcemanager_v3
    from google.iam.v1 import iam_policy_pb2, policy_pb2

    client = resourcemanager_v3.ProjectsClient()
    policy = client.get_iam_policy(
        request=iam_policy_pb2.GetIamPolicyRequest(resource=f"projects/{project}")
    )
    existing = {(b.role, m) for b in policy.bindings for m in b.members}
    changed = False
    for role in BASELINE_ROLES:
        if (role, principal) in existing:
            continue
        policy.bindings.append(policy_pb2.Binding(role=role, members=[principal]))
        changed = True
        print(f"  + project IAM {role}")
    if changed:
        client.set_iam_policy(
            request=iam_policy_pb2.SetIamPolicyRequest(
                resource=f"projects/{project}", policy=policy
            )
        )
    else:
        print("  (project IAM already present)")


def _grant_endpoint_egress(project: str, location: str, principal: str) -> None:
    raw = _run(
        [
            "gcloud",
            "agent-registry",
            "services",
            "list",
            f"--project={project}",
            f"--location={location}",
            "--format=json",
        ]
    )
    for item in json.loads(raw or "[]"):
        registry = item.get("registryResource") or ""
        if not registry:
            continue
        endpoint_id = registry.rsplit("/", 1)[-1]
        print(f"  + iap.egressor on {item.get('name')} ({endpoint_id})")
        subprocess.run(
            [
                "gcloud",
                "beta",
                "iap",
                "web",
                "add-iam-policy-binding",
                "--resource-type=agent-registry",
                f"--endpoint={endpoint_id}",
                f"--region={location}",
                f"--project={project}",
                f"--member={principal}",
                "--role=roles/iap.egressor",
                "--condition=None",
                "--quiet",
            ],
            check=False,
            capture_output=True,
            text=True,
        )


def _assert_identity(effective: str | None) -> str:
    if not effective:
        raise SystemExit("effectiveIdentity missing")
    if "@" in effective or "gcp-sa-aiplatform" in effective:
        raise SystemExit(f"effectiveIdentity is not agent identity: {effective}")
    prefix = f"agents.global.org-{ORG_ID}"
    if not effective.startswith(prefix):
        raise SystemExit(f"unexpected trust domain: {effective}")
    print(f"effectiveIdentity OK: {effective}")
    return effective


def _delete_engine(client: object, name: str) -> None:
    """Delete via Vertex SDK (gcloud has no stable reasoning-engines command)."""
    print(f"Deleting previous engine {name}")
    try:
        client.agent_engines.delete(name=name)  # type: ignore[attr-defined]
        print("  deleted")
    except Exception as exc:  # noqa: BLE001
        print(f"  delete warning: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="countersign-505107")
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--display-name", default="countersign-hello")
    parser.add_argument(
        "--agent-gateway",
        default="projects/countersign-505107/locations/us-central1/agentGateways/countersign-egress",
    )
    parser.add_argument("--staging-bucket", default=None)
    parser.add_argument("--model", default="gemini-3.5-flash")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete any existing engine with this display name first",
    )
    args = parser.parse_args()

    staging_bucket = args.staging_bucket or f"gs://{args.project}-agent-staging"
    _ensure_bucket(args.project, args.region, staging_bucket)

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
    for a in client.agent_engines.list():
        if a.api_resource.display_name == args.display_name:
            existing_name = a.api_resource.name
            break

    if existing_name and args.recreate:
        _delete_engine(client, existing_name)
        existing_name = None
        # Brief wait so the display name can be reused.
        import time

        time.sleep(5)

    # Stage package first — import must happen from staging (official packaging rule).
    staging_dir = tempfile.mkdtemp(prefix="countersign_hello_deploy_")
    original_cwd = os.getcwd()
    try:
        shutil.copytree(
            HELLO_DIR / "agent",
            Path(staging_dir) / "agent",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
        os.chdir(staging_dir)
        # Ensure a clean import of staged package only.
        sys.path = [staging_dir] + [p for p in sys.path if p not in (str(HELLO_DIR), "")]
        for mod in list(sys.modules):
            if mod == "agent" or mod.startswith("agent."):
                del sys.modules[mod]

        os.environ["MODEL_NAME"] = args.model
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
        from agent.agent import root_agent  # noqa: WPS433 — must import after staging

        app = AdkApp(agent=root_agent, enable_tracing=True)

        deploy_config: dict = {
            "staging_bucket": staging_bucket,
            # Match official identity doc requirements shape.
            "requirements": [
                "google-cloud-aiplatform[adk,agent_engines]",
                "google-adk",
                "cloudpickle",
                "pydantic",
            ],
            "extra_packages": ["agent"],
            "env_vars": {
                "GOOGLE_GENAI_USE_VERTEXAI": "true",
                "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
                # Codelab mortgage-agent bootstrap for gateway egress.
                "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES": "false",
                "MODEL_NAME": args.model,
                "GOOGLE_CLOUD_LOCATION": args.region,
            },
            "display_name": args.display_name,
            "description": "Countersign hello Runtime agent for Agent Gateway governance proof.",
            "identity_type": types.IdentityType.AGENT_IDENTITY,
            "agent_gateway_config": {
                "agent_to_anywhere_config": {"agent_gateway": args.agent_gateway}
            },
            "min_instances": 1,
            "max_instances": 2,
            "resource_limits": {"cpu": "1", "memory": "4Gi"},
        }

        if existing_name:
            print(f"Updating existing {existing_name}")
            engine = client.agent_engines.update(
                name=existing_name, agent=app, config=deploy_config
            )
        else:
            print("Step 1: create AGENT_IDENTITY shell")
            shell = client.agent_engines.create(
                config={
                    "display_name": args.display_name,
                    "description": deploy_config["description"],
                    "identity_type": types.IdentityType.AGENT_IDENTITY,
                }
            )
            effective = _assert_identity(shell.api_resource.spec.effective_identity)
            principal = f"principal://{effective}"
            print("Step 2: grant project IAM + endpoint egress")
            _grant_project_roles(args.project, principal)
            _grant_endpoint_egress(args.project, args.region, principal)
            print("Step 3: update with AdkApp + agent_gateway_config")
            engine = client.agent_engines.update(
                name=shell.api_resource.name, agent=app, config=deploy_config
            )
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(staging_dir, ignore_errors=True)

    engine = client.agent_engines.get(name=engine.api_resource.name)
    spec = engine.api_resource.spec
    effective = _assert_identity(spec.effective_identity)
    gateway = getattr(getattr(spec, "deployment_spec", None), "agent_gateway_config", None)
    if gateway is None:
        raise SystemExit("agentGatewayConfig missing after deploy")
    print(f"agentGatewayConfig OK: {gateway}")

    _grant_project_roles(args.project, f"principal://{effective}")
    _grant_endpoint_egress(args.project, args.region, f"principal://{effective}")

    meta = {
        "remote_agent_runtime_id": engine.api_resource.name,
        "deployment_target": "agent_runtime",
        "deploy_mode": "sdk_adkapp_source",
        "is_a2a": False,
        "agent_directory": "infra/geap/hello_runtime/agent",
        "deployment_timestamp": datetime.now(timezone.utc).isoformat(),
        "effective_identity": effective,
        "agent_gateway": args.agent_gateway,
        "project_number": _project_number(args.project),
    }
    (ROOT / "deployment_metadata.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
