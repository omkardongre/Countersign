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

"""Countersign GMP deviation-investigation domain workflow.

This package holds the deterministic, GMP-domain-specific graph described in
``final_reseach/05_architecture.md`` section 5 — deterministic intake and
classification, evidence extraction, the compliance engine, the investigation
and challenger LLM agents, and the QP sign-off gate. It is separate from
``app.agent`` (the platform-facing ADK entrypoint) so the domain logic can be
unit tested without a running gateway or model backend.
"""
