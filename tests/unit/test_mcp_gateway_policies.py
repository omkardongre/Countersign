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

"""Gateway MCP tool-name policy artifacts must keep the session handshake trap closed."""

import pathlib

import yaml

_GEAP = pathlib.Path(__file__).resolve().parents[2] / "infra" / "geap"

# The department MCP service these tool-name policies are scoped to. Kept as a
# literal so a change to the deployed MCP host has to be made deliberately in
# both the policy and this test.
_MCP_HOST = "countersign-department-mcp-777448403573.us-central1.run.app"


def _read(name: str) -> str:
    return (_GEAP / name).read_text(encoding="utf-8")


def _operations(text: str) -> list[dict]:
    policy = yaml.safe_load(text)
    ops: list[dict] = []
    for rule in policy.get("httpRules") or []:
        ops.extend((rule.get("to") or {}).get("operations") or [])
    return ops


def _exact_tools(text: str) -> set[str]:
    """Tool names allowed by ``tools/call``, ignoring host and other matchers.

    Reads the parsed structure rather than grepping every ``exact:`` in the
    file, so host matchers cannot be mistaken for tool names.
    """
    tools: set[str] = set()
    for op in _operations(text):
        for method in (op.get("mcp") or {}).get("methods") or []:
            for param in method.get("params") or []:
                if "exact" in param:
                    tools.add(param["exact"])
    return tools


def _hosts(text: str) -> set[str]:
    hosts: set[str] = set()
    for op in _operations(text):
        for host in op.get("hosts") or []:
            if "exact" in host:
                hosts.add(host["exact"])
    return hosts


def test_case_workflow_policy_allows_all_named_tools() -> None:
    """Department reads plus the sterility-clock tools the workflow calls.

    Sterility tools are here because an Agent Identity principal cannot be a
    Cloud SQL IAM database user (confirmed against the live instance), so
    the clock is reached through MCP like LIMS/EM/CoA, not opened directly.
    """
    text = _read("mcp-case-workflow-tools-allow.yaml")
    assert "MATCH_BASE_PROTOCOL_METHODS" in text
    assert 'name: "tools/list"' in text
    assert "bind_to: case_workflow" in text
    assert "do_not_bind_to: qc_probe" in text
    assert _exact_tools(text) == {
        "lims_query",
        "em_query",
        "coa_fetch",
        "sterility_get",
        "sterility_start",
        "sterility_bind_resume_handle",
        "prior_art_query",
        "prior_art_write",
    }


def test_qc_probe_allows_lims_only_for_wrong_department_deny() -> None:
    text = _read("mcp-qc-probe-lims-only.yaml")
    assert "MATCH_BASE_PROTOCOL_METHODS" in text
    assert "bind_to: qc_probe" in text
    assert "do_not_bind_to: case_workflow" in text
    assert _exact_tools(text) == {"lims_query"}


def test_mcp_tool_policies_are_scoped_to_the_mcp_host() -> None:
    """An MCP tool-name ALLOW must not be the only rule governing the gateway.

    These policies use ``action: ALLOW`` and target the whole
    ``countersign-egress`` gateway. Google's evaluation order says a request
    matching no ALLOW rule is denied, and an ``mcp:`` matcher cannot match
    non-MCP traffic, so an unscoped rule here denies every other call the
    Runtime makes — including its own Vertex session writes. That is exactly
    what broke async_create_session live (403 "RBAC: access denied", gateway
    detail ``denied_as_no_allow_policies_matched_request``). Scoping by host
    keeps this file's blast radius on the MCP service.
    """
    for name in ("mcp-case-workflow-tools-allow.yaml", "mcp-qc-probe-lims-only.yaml"):
        text = _read(name)
        assert _hosts(text) == {_MCP_HOST}, name
        for op in _operations(text):
            # Every operation carrying an mcp matcher must also be host-scoped.
            if op.get("mcp"):
                assert op.get("hosts"), f"{name}: mcp matcher without a host scope"


def test_aiplatform_sessions_policy_allows_the_mtls_hosts() -> None:
    """Vertex-managed sessions and telemetry must be allowed through the gateway.

    An Agent Identity presents an mTLS-bound certificate, so its egress lands
    on the ``.mtls.`` hostnames — confirmed from the gateway log
    (``enforcedGatewaySecurityPolicy.hostname:
    us-central1-aiplatform.mtls.googleapis.com``). Allowing only the plain
    hosts looks correct and still denies every session call, so the mTLS
    variants are asserted explicitly.
    """
    text = _read("aiplatform-sessions-allow.yaml")
    hosts = _hosts(text)
    assert "us-central1-aiplatform.mtls.googleapis.com" in hosts
    assert "aiplatform.mtls.googleapis.com" in hosts
    assert "telemetry.mtls.googleapis.com" in hosts
    assert "iamcredentials.mtls.googleapis.com" in hosts
    assert yaml.safe_load(text)["action"] == "ALLOW"
    # This policy is host-scoped only; it must never carry a tool-name matcher,
    # which would make MCP payload parsing a condition of session traffic.
    assert _exact_tools(text) == set()


def test_policy_bindings_forbid_probe_on_case_workflow() -> None:
    text = _read("mcp-tool-policy-bindings.yaml")
    assert "identity: case_workflow" in text
    assert "identity: qc_probe" in text
    assert "mcp-qc-probe-lims-only.yaml" in text
    assert "forbidden_bindings:" in text
    case_probe = False
    in_forbidden = False
    policy = ""
    identity = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "forbidden_bindings:":
            in_forbidden = True
            continue
        if not in_forbidden:
            continue
        if stripped.startswith("- policy:"):
            policy = stripped.split(":", 1)[1].strip()
            identity = ""
        elif stripped.startswith("identity:"):
            identity = stripped.split(":", 1)[1].strip()
            if (
                policy == "mcp-qc-probe-lims-only.yaml"
                and identity == "case_workflow"
            ):
                case_probe = True
    assert case_probe


def test_split_collector_identity_policies_are_not_in_tree() -> None:
    assert not (_GEAP / "mcp-qc-tools-allow.yaml").exists()
    assert not (_GEAP / "mcp-supply-tools-allow.yaml").exists()
