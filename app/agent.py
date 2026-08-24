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

"""Platform entrypoint: the joined deviation-case Workflow.

``fast_api_app`` and the ADK runner load ``root_agent`` from this module.
Resumability is on so sterility hold and QP ``RequestInput`` continue by
invocation id. Sessions persist via ``SESSION_SERVICE_URI`` when set.
The GEAP hello agent used for Gateway deny proof stays isolated under
``infra/geap/hello_runtime`` and is not this graph.
"""

from google.adk.apps import App, ResumabilityConfig

from app.domain.case_graph import build_deviation_case_workflow
from app.domain.cfi_graph import build_cfi_workflow

root_agent = build_deviation_case_workflow()

app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True),
)

# The CFI investigation graph is deployed as its own Agent Runtime, with its
# own Agent Identity and Gateway allow-list, disjoint from the case
# workflow's (13_cfi_extended_investigation_plan.md section 4.1). It is a
# second app in this same module, not a branch of `root_agent`/`app` above —
# those stay byte-identical to before this graph existed.
cfi_root_agent = build_cfi_workflow()

cfi_app = App(
    root_agent=cfi_root_agent,
    name="cfi_app",
    resumability_config=ResumabilityConfig(is_resumable=True),
)
