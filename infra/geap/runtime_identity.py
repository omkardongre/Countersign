#!/usr/bin/env python3
"""Agent Identity helpers shared by Agent Runtime deploys.

Extracted from the proven hello deploy so the case workflow gets the same
identity assertions and IAM grants. Both follow:
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-identity
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-gateway-runtime-deploy
"""

from __future__ import annotations

import json
import os
import subprocess

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

def run(cmd: list[str]) -> str:
    return subprocess.run(
        cmd, check=True, capture_output=True, text=True
    ).stdout.strip()


def project_number(project: str) -> str:
    return run(
        ["gcloud", "projects", "describe", project, "--format=value(projectNumber)"]
    )


def ensure_bucket(project: str, location: str, bucket: str) -> None:
    name = bucket.removeprefix("gs://")
    described = subprocess.run(
        [
            "gcloud",
            "storage",
            "buckets",
            "describe",
            f"gs://{name}",
            f"--project={project}",
        ],
        capture_output=True,
        text=True,
    )
    if described.returncode == 0:
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


def assert_identity(effective: str | None) -> str:
    """Fail loudly when the Runtime fell back to the default service account."""
    if not effective:
        raise SystemExit("effectiveIdentity missing")
    if "@" in effective or "gcp-sa-aiplatform" in effective:
        raise SystemExit(f"effectiveIdentity is not agent identity: {effective}")
    prefix = f"agents.global.org-{ORG_ID}"
    if not effective.startswith(prefix):
        raise SystemExit(f"unexpected trust domain: {effective}")
    print(f"effectiveIdentity OK: {effective}")
    return effective


def grant_project_roles(project: str, principal: str, roles: list[str]) -> None:
    from google.cloud import resourcemanager_v3
    from google.iam.v1 import iam_policy_pb2, policy_pb2

    client = resourcemanager_v3.ProjectsClient()
    policy = client.get_iam_policy(
        request=iam_policy_pb2.GetIamPolicyRequest(resource=f"projects/{project}")
    )
    existing = {(b.role, m) for b in policy.bindings for m in b.members}
    changed = False
    for role in roles:
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


def grant_endpoint_egress(project: str, location: str, principal: str) -> None:
    """Registered destinations are deny-by-default until iap.egressor is held."""
    raw = run(
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
