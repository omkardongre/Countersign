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
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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


def _iap_request(url: str, payload: dict[str, object]) -> dict[str, object]:
    """Call the IAP REST API using the deployer's ADC, failing closed."""
    token = run(["gcloud", "auth", "application-default", "print-access-token"])
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except (HTTPError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"IAP policy request failed for {url}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"IAP policy response was not an object for {url}")
    return parsed


def _grant_mcp_egress(
    *, project_number_value: str, location: str, mcp_server_id: str, principal: str
) -> None:
    """Grant the Runtime identity access to one registered MCP server.

    The gcloud IAP command supports Agent Registry *endpoints*, but an MCP
    TOOL_SPEC is an ``mcpServers`` resource.  Treating both as endpoints used
    to fail silently, leaving ``initialize`` possible but denying every
    ``tools/call`` request at IAP.  Use the documented per-MCP REST path and
    preserve every existing binding.
    """
    base = (
        "https://iap.googleapis.com/v1/"
        f"projects/{project_number_value}/locations/{location}/iap_web/agentRegistry/"
        f"mcpServers/{mcp_server_id}"
    )
    current = _iap_request(f"{base}:getIamPolicy", {})
    policy = {
        "version": int(current.get("version", 1)),
        "etag": str(current.get("etag", "")),
        "bindings": list(current.get("bindings", [])),
    }
    found = False
    for binding in policy["bindings"]:
        if not isinstance(binding, dict) or binding.get("role") != "roles/iap.egressor":
            continue
        members = binding.setdefault("members", [])
        if not isinstance(members, list):
            raise RuntimeError("roles/iap.egressor members must be a list")
        if principal not in members:
            members.append(principal)
        found = True
        break
    if not found:
        policy["bindings"].append(
            {"role": "roles/iap.egressor", "members": [principal]}
        )
    _iap_request(f"{base}:setIamPolicy", {"policy": policy})


def grant_endpoint_egress(project: str, location: str, principal: str) -> None:
    """Grant IAP egress on every registered endpoint and MCP server.

    Agent Gateway is deny-by-default.  MCP TOOL_SPEC registry resources need
    their own ``mcpServers`` IAM binding; the endpoint-only CLI path is not a
    substitute.
    """
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
    number = project_number(project)
    for item in json.loads(raw or "[]"):
        registry = item.get("registryResource") or ""
        if not registry:
            continue
        # Registry also lists agent cards.  They are discoverable peers, not
        # egress destinations, and IAP has no ``agentRegistry/agents`` policy
        # path.  Trying to bind them makes a case Runtime deployment fail
        # after the old engine has been replaced.
        if not item.get("endpointSpec") and "/mcpServers/" not in registry:
            print(f"  = no egress binding for non-endpoint {item.get('name')}")
            continue
        endpoint_id = registry.rsplit("/", 1)[-1]
        print(f"  + iap.egressor on {item.get('name')} ({endpoint_id})")
        if "/mcpServers/" in registry:
            _grant_mcp_egress(
                project_number_value=number,
                location=location,
                mcp_server_id=endpoint_id,
                principal=principal,
            )
            continue
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
            check=True,
        )
