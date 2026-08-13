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

"""Entrypoint test: ``app.agent.root_agent`` is the joined case workflow."""

from app.agent import app as adk_app
from app.agent import root_agent
from app.domain.case_graph import CASE_WORKFLOW_NAME
from google.adk.workflow import Workflow


def test_root_agent_is_the_deviation_case_workflow() -> None:
    assert isinstance(root_agent, Workflow)
    assert root_agent.name == CASE_WORKFLOW_NAME
    assert adk_app.root_agent is root_agent
