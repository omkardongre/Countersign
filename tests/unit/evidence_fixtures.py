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

"""Synthetic fixtures and ground truth for the evidence-extraction slice.

All pharmaceutical content here is **synthetic**. Batch numbers, product codes,
equipment ids, operator initials and process values are invented. Putting real
GMP batch data into a test suite would itself be the compliance problem, and the
architecture's honesty ledger says so explicitly.

``GROUND_TRUTH_READINGS`` is a clean, in-specification, high-confidence page.
Every test that exercises a failure mode starts from it and perturbs one thing,
so a failing assertion names exactly one cause.
"""

from __future__ import annotations

import base64
import hashlib

from app.domain.evidence_models import (
    BoundingBox,
    EvidenceIngestRequest,
    ExtractedEvidence,
    ExtractedField,
    IngestedArtifact,
    ModelFieldReading,
    PageExtraction,
    SourceAnchor,
)
from app.domain.extraction import EXTRACTOR_AGENT_NAME, EXTRACTOR_MODEL
from app.domain.master_data import load_document_field_spec

# Smallest valid PNG. Content is irrelevant: no test in this suite asks a model
# to read it. What is exercised is the deterministic pipeline around the model,
# with the model's answers supplied as known ground truth.
SYNTHETIC_PAGE_BYTES: bytes = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

TENANT_ID = "aurex"
CASE_ID = "case-evidence-1"
BATCH_ID = "AX-2291"
SOURCE_URI = "drive://synthetic/bmr/AX-2291/page-14"


def make_ingest_request(**overrides) -> EvidenceIngestRequest:
    """Builds a valid ingest request, with any field overridden.

    Uses ``model_construct`` when an override would fail model validation, so
    tests can exercise the deterministic integrity checks rather than only
    Pydantic's.
    """
    payload = {
        "tenant_id": TENANT_ID,
        "case_id": CASE_ID,
        "batch_id": BATCH_ID,
        "source_uri": SOURCE_URI,
        "mime_type": "image/png",
        "content_base64": base64.b64encode(SYNTHETIC_PAGE_BYTES).decode(),
        "page_count": 1,
    }
    payload.update(overrides)
    return EvidenceIngestRequest.model_construct(**payload)


def make_ingested_artifact(**overrides) -> IngestedArtifact:
    """Builds an artifact reference matching the synthetic page."""
    spec = load_document_field_spec()
    digest = hashlib.sha256(SYNTHETIC_PAGE_BYTES).hexdigest()
    payload = {
        "tenant_id": TENANT_ID,
        "case_id": CASE_ID,
        "batch_id": BATCH_ID,
        "artifact_filename": f"evidence__{TENANT_ID}__{CASE_ID}__{digest[:16]}.png",
        "artifact_version": 0,
        "artifact_sha256": digest,
        "mime_type": "image/png",
        "byte_size": len(SYNTHETIC_PAGE_BYTES),
        "page_count": 1,
        "source_uri": SOURCE_URI,
        "spec_id": spec.spec_id,
        "spec_version": spec.spec_version,
        "spec_sha256": spec.spec_sha256,
    }
    payload.update(overrides)
    return IngestedArtifact(**payload)


def _reading(
    field_name: str,
    value_text: str,
    *,
    unit_text: str | None = None,
    confidence: float = 0.99,
    page_number: int = 1,
) -> ModelFieldReading:
    return ModelFieldReading(
        field_name=field_name,
        value_text=value_text,
        unit_text=unit_text,
        confidence=confidence,
        page_number=page_number,
        bounding_box=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.15),
        transcribed_context=f"{field_name}: {value_text}",
    )


GROUND_TRUTH_READINGS: tuple[ModelFieldReading, ...] = (
    _reading("batch_number", "AX-2291"),
    _reading("product_code", "AXR450"),
    _reading("page_number", "14"),
    _reading("manufacturing_step", "blending"),
    _reading("equipment_id", "EQ-114"),
    _reading("step_start_time", "2026-03-04 02:10"),
    _reading("step_end_time", "2026-03-04 02:25"),
    _reading("blend_speed_rpm", "11.5", unit_text="rpm"),
    _reading("blend_time_min", "15", unit_text="min"),
    _reading("product_temperature_c", "22.4", unit_text="degC"),
    _reading("operator_initials", "JMR"),
    _reading("verifier_initials", "PLK"),
    _reading("deviation_noted", "no"),
)


def make_page_extraction(
    *,
    replace: dict[str, ModelFieldReading] | None = None,
    drop: set[str] | None = None,
    add: list[ModelFieldReading] | None = None,
    unreadable: list[str] | None = None,
) -> PageExtraction:
    """Builds extractor output from ground truth with targeted perturbations."""
    replace = replace or {}
    drop = drop or set()
    readings: list[ModelFieldReading] = []
    for reading in GROUND_TRUTH_READINGS:
        if reading.field_name in drop:
            continue
        readings.append(replace.get(reading.field_name, reading))
    readings.extend(add or [])
    return PageExtraction(
        readings=readings, unreadable_field_names=unreadable or []
    )


def make_extracted_evidence(
    *,
    fields: list[ExtractedField] | None = None,
    unreadable_field_names: list[str] | None = None,
    unspecified_field_names: list[str] | None = None,
    artifact: IngestedArtifact | None = None,
) -> ExtractedEvidence:
    """Builds a provenance-stamped evidence record for validation tests."""
    artifact = artifact or make_ingested_artifact()
    spec = load_document_field_spec()
    if fields is None:
        fields = [
            extracted_field_from_reading(artifact, reading)
            for reading in GROUND_TRUTH_READINGS
        ]
    return ExtractedEvidence(
        tenant_id=artifact.tenant_id,
        case_id=artifact.case_id,
        batch_id=artifact.batch_id,
        artifact_filename=artifact.artifact_filename,
        artifact_version=artifact.artifact_version,
        artifact_sha256=artifact.artifact_sha256,
        page_count=artifact.page_count,
        spec_id=spec.spec_id,
        spec_version=spec.spec_version,
        spec_sha256=spec.spec_sha256,
        extractor_agent=EXTRACTOR_AGENT_NAME,
        extractor_model=EXTRACTOR_MODEL,
        fields=fields,
        unreadable_field_names=unreadable_field_names or [],
        unspecified_field_names=unspecified_field_names or [],
    )


def extracted_field_from_reading(
    artifact: IngestedArtifact, reading: ModelFieldReading
) -> ExtractedField:
    """Stamps artifact provenance onto a reading, as the extraction node does."""
    return ExtractedField(
        field_name=reading.field_name,
        value_text=reading.value_text,
        unit_text=reading.unit_text,
        confidence=reading.confidence,
        anchor=SourceAnchor(
            artifact_filename=artifact.artifact_filename,
            artifact_version=artifact.artifact_version,
            artifact_sha256=artifact.artifact_sha256,
            source_uri=artifact.source_uri,
            page_number=reading.page_number,
            bounding_box=reading.bounding_box,
            transcribed_context=reading.transcribed_context,
        ),
        legibility_note=reading.legibility_note,
    )


def replace_field(
    evidence: ExtractedEvidence, field: ExtractedField
) -> ExtractedEvidence:
    """Returns a copy of ``evidence`` with one field swapped out."""
    fields = [
        field if existing.field_name == field.field_name else existing
        for existing in evidence.fields
    ]
    return evidence.model_copy(update={"fields": fields})


def make_validated_evidence(**extracted_overrides):
    """Builds a clean, fully-accepted ``ValidatedEvidence`` for the ground
    truth page, for tests one layer downstream of extraction (compliance).

    Runs the real ``validate_extracted_evidence`` rather than constructing a
    ``ValidatedEvidence`` by hand, so compliance tests exercise the actual
    validation output shape instead of a hand-maintained approximation of it.
    """
    from app.domain.evidence_validation import validate_extracted_evidence

    evidence = make_extracted_evidence(**extracted_overrides)
    return validate_extracted_evidence(evidence, page_count=evidence.page_count)
