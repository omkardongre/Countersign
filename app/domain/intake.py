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

"""Deterministic intake node: CaseIntake -> ClassifiedCase.

Uses the default ``parameter_binding="state"`` mode: with a single
``node_input: CaseIntake`` parameter, ADK infers ``input_schema`` from the
parameter's type hint and ``output_schema`` from the ``-> ClassifiedCase``
return hint (verified against installed ``google-adk`` 2.6.3,
``workflow/_function_node.py::_infer_schemas_for_state_mode``), so the
workflow enforces both schemas without any separate declaration.
(``parameter_binding="node_input"`` is for functions with multiple named
parameters bound from a dict, which does not apply here.) This is a plain
``FunctionNode`` — no ``LlmAgent`` — because intake and classification are
deterministic per ``final_reseach/05_architecture.md`` section 5
("Deterministic intake | code node | classify doc type... route").
"""

from __future__ import annotations

from google.adk.workflow import node

from app.domain.classification import classify_severity
from app.domain.models import CaseIntake, ClassifiedCase, LifecycleState


def classify_intake(intake: CaseIntake) -> ClassifiedCase:
    """Assigns GMP deviation severity and opens a classified case record.

    Pure construction over ``classify_severity``. Kept as a function so the
    intake node and the joined case graph share one classification path.
    """
    severity, rationale = classify_severity(intake)
    return ClassifiedCase(
        tenant_id=intake.tenant_id,
        batch_id=intake.batch_id,
        product_name=intake.product_name,
        doc_type=intake.doc_type,
        severity=severity,
        lifecycle_state=LifecycleState.CLASSIFIED,
        evidence_refs=intake.evidence_refs,
        rationale=rationale,
    )


@node(name="intake_and_classify")
def intake_and_classify(node_input: CaseIntake) -> ClassifiedCase:
    """Creates a case record and assigns its GMP deviation severity.

    Args:
        node_input: The raw detection payload reported at containment time.

    Returns:
        A ``ClassifiedCase`` with ``lifecycle_state=CLASSIFIED`` and
        ``decision_authority="none"`` — ready for the Gateway's five-tuple
        authorization on subsequent steps.
    """
    return classify_intake(node_input)
