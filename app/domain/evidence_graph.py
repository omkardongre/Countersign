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

"""The evidence-extraction and compliance-check graph.

A static ADK 2.x ``Workflow``, not an LLM orchestrator. ADK's own guidance is to
use a ``Workflow`` when the execution sequence is predefined and you need
deterministic paths, strict compliance and predictable failure states. Batch
record review is that sentence, so the sequence is fixed in the graph and the
only model in it sits inside one node:

    START -> ingest_evidence_artifact     (deterministic: allow-list, hash, persist)
          -> extract_page_evidence        (one narrow single-turn transcriber)
          -> validate_page_evidence       (deterministic: parse, spec, confidence)
          -> evaluate_evidence_compliance (deterministic: completeness, OOS, OOT)

Each node passes exactly one typed payload to the next, and the payload shrinks
as it goes: raw bytes never leave the ingest node, and only a hash-verified
reference reaches the extractor.

Parallel evidence branches — MES/BMR, LIMS and environmental monitoring,
supplier CoA — attach later as sibling paths joined by a ``JoinNode`` before the
compliance engine, which is already written to accept one ``ValidatedEvidence``
per artifact and would need a small reconciliation step, not a redesign, to
accept a joined dict of them. They are deliberately not wired yet: this graph is
the one vertical slice that can be tested against ground truth, and a fan-in
whose branches are not individually trustworthy is not worth building.
"""

from __future__ import annotations

from google.adk.workflow import START, Workflow

from app.domain.compliance import evaluate_evidence_compliance
from app.domain.evidence_validation import validate_page_evidence
from app.domain.extraction import extract_page_evidence
from app.domain.ingest import ingest_evidence_artifact

EVIDENCE_WORKFLOW_NAME = "evidence_extraction"


def build_evidence_extraction_workflow() -> Workflow:
    """Builds the evidence-extraction-and-compliance graph.

    Returns:
        A ``Workflow`` taking an ``EvidenceIngestRequest`` at ``START`` and
        producing a ``ComplianceResult``. Node input and output schemas are
        inferred by ADK from the node type hints, so a payload that does not
        match the next node's contract fails at the graph boundary rather than
        inside domain logic.
    """
    return Workflow(
        name=EVIDENCE_WORKFLOW_NAME,
        edges=[
            (START, ingest_evidence_artifact),
            (ingest_evidence_artifact, extract_page_evidence),
            (extract_page_evidence, validate_page_evidence),
            (validate_page_evidence, evaluate_evidence_compliance),
        ],
    )
