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
import re

_GEAP = pathlib.Path(__file__).resolve().parents[2] / "infra" / "geap"
_EXACT = re.compile(r'exact:\s*"([^"]+)"')


def _read(name: str) -> str:
    return (_GEAP / name).read_text(encoding="utf-8")


def _exact_tools(text: str) -> set[str]:
    return set(_EXACT.findall(text))


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
