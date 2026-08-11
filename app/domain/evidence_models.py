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

"""Schemas for source-anchored multimodal evidence extraction.

Three separations are load-bearing here, and each one exists because of a
specific finding in the research, not for tidiness:

1. **The model transcribes; code interprets.** ``ModelFieldReading`` carries
   only characters the extractor claims to have read, as a string, plus where
   on the page it read them. Every parse into a number, a boolean or a
   timestamp happens in deterministic validation code. A model that is allowed
   to emit ``12.0`` for a field it actually read as ``1.2`` has silently
   created a GMP data-integrity problem; a model that emits the string
   ``"1.2"`` and a confidence has merely reported what it saw.

2. **Artifact identity is stamped, never claimed.** The extractor cannot
   populate ``SourceAnchor.artifact_filename``/``artifact_version``/
   ``artifact_sha256`` — deterministic code does, from the artifact it actually
   loaded and hash-verified. Otherwise provenance would be model output, which
   is the one thing provenance must never be.

3. **Every fact carries its source.** Katalyze's connected-record and
   traceability material and MasterControl's exception-review material both
   land on the same requirement: a finding without a pointer back to the
   originating record is not usable in an investigation. A reading with no
   anchor fails validation here rather than travelling onward unsourced.

``decision_authority`` stays ``"none"`` on every record produced in this
module, consistent with ``app.domain.models`` and the draft Annex 22 posture
recorded in ``final_reseach/05_architecture.md`` section 0.
"""

from __future__ import annotations

import datetime
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# Allow-list for evidence the extraction slice will accept. Deliberately
# narrow: an ingest path that accepts anything is an ingest path that will
# eventually be handed something executable. PDF and the raster formats cover
# scanned and photographed batch-record pages, which is what this stage reads.
ALLOWED_EVIDENCE_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/webp",
    }
)

# Hard ceiling on a single ingested evidence payload. MasterControl notes batch
# records commonly run 400-500 pages; those arrive as many page-scoped
# artifacts, not one blob, so a single artifact exceeding this bound means the
# caller is doing something the downstream nodes were not designed for.
MAX_EVIDENCE_BYTES: int = 20 * 1024 * 1024


class FieldStatus(StrEnum):
    """Outcome of deterministic validation for one field."""

    ACCEPTED = "accepted"
    """Parsed, in-spec, and at or above the required confidence."""

    REVIEW_REQUIRED = "review_required"
    """Readable but not trustworthy enough to accept unverified."""

    INVALID = "invalid"
    """Parsed or range/pattern/unit check failed. Always human-reviewed."""

    MISSING = "missing"
    """Required by the spec and not returned by the extractor."""


class EvidenceStatus(StrEnum):
    """Outcome of deterministic validation for the whole page.

    There is no ``auto_rejected`` state. Integrity failures — tenant mismatch,
    hash mismatch, disallowed MIME type — raise before an evidence record is
    ever produced, because a compliance boundary must not be expressible as a
    field an LLM or a downstream node could negotiate with. What remains is a
    binary: good enough to use unverified, or a human looks at it.
    """

    ACCEPTED = "accepted"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


class BoundingBox(BaseModel):
    """Normalised (0-1) region of the page a reading was taken from.

    Normalised rather than pixel coordinates so an anchor survives the page
    being re-rendered at a different resolution — a reviewer opening the
    citation a year later must land on the same region.
    """

    model_config = ConfigDict(frozen=True)

    x_min: float = Field(ge=0.0, le=1.0)
    y_min: float = Field(ge=0.0, le=1.0)
    x_max: float = Field(ge=0.0, le=1.0)
    y_max: float = Field(ge=0.0, le=1.0)

    @property
    def is_well_formed(self) -> bool:
        """True when the box encloses a positive area."""
        return self.x_max > self.x_min and self.y_max > self.y_min


class ModelFieldReading(BaseModel):
    """One field as the extractor claims to have read it.

    This is the extractor's ``output_schema``. It contains no artifact
    identity, no typed value and no validation verdict — only a transcription,
    a location, and a self-reported confidence.
    """

    field_name: str = Field(
        description="Field name from the approved document specification."
    )
    value_text: str = Field(
        description=(
            "Characters as read, verbatim, with no normalisation, rounding, "
            "unit conversion or spelling correction."
        )
    )
    unit_text: str | None = Field(
        default=None,
        description="Unit as printed next to the value, verbatim, or null.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Legibility confidence for this specific field. Low confidence is "
            "the correct answer for smudged or ambiguous handwriting."
        ),
    )
    page_number: int = Field(
        ge=1, description="1-based page the value was read from."
    )
    bounding_box: BoundingBox | None = Field(
        default=None,
        description="Normalised region enclosing the value on that page.",
    )
    transcribed_context: str = Field(
        default="",
        description=(
            "Short verbatim snippet of surrounding text, so a reviewer can "
            "locate the value even if the box is imprecise."
        ),
    )
    legibility_note: str = Field(
        default="",
        description="Why confidence is low, when it is. Empty otherwise.",
    )


class PageExtraction(BaseModel):
    """Complete extractor output for one document page."""

    readings: list[ModelFieldReading] = Field(
        default_factory=list,
        description=(
            "One entry per field found. Fields that are absent or illegible "
            "must be omitted rather than guessed."
        ),
    )
    unreadable_field_names: list[str] = Field(
        default_factory=list,
        description=(
            "Specified fields that are present on the page but cannot be read "
            "at all. Reported explicitly so the gap is visible, not inferred "
            "from silence."
        ),
    )


class SourceAnchor(BaseModel):
    """Where a fact came from — artifact identity plus page location.

    The artifact fields are stamped by deterministic code from the artifact it
    loaded and hash-verified, so an anchor cannot be fabricated by the model.
    """

    model_config = ConfigDict(frozen=True)

    artifact_filename: str
    artifact_version: int
    artifact_sha256: str
    source_uri: str = Field(
        description="Where the original record was retrieved from."
    )
    page_number: int
    bounding_box: BoundingBox | None = None
    transcribed_context: str = ""


class EvidenceIngestRequest(BaseModel):
    """Request to bring one page of source evidence under case control."""

    tenant_id: str = Field(
        description="Sponsor scope. Checked before anything is persisted."
    )
    case_id: str
    batch_id: str
    source_uri: str = Field(
        description=(
            "Where this record came from (Drive file, MES export, LIMS "
            "attachment). Recorded on every anchor derived from it."
        )
    )
    mime_type: str
    content_base64: str = Field(
        description=(
            "Payload, base64. Transported this way only at the ingest "
            "boundary; from the next node onward only the artifact reference "
            "travels, because ADK's own guidance is that session state is not "
            "for large binary payloads."
        )
    )
    page_count: int = Field(
        ge=1,
        description=(
            "Pages in this artifact, as declared by the retrieving connector. "
            "Bounds the page numbers an extractor is allowed to cite."
        ),
    )
    spec_version_expected: str | None = Field(
        default=None,
        description=(
            "If set, ingest fails unless the loaded approved spec matches. "
            "Lets a caller pin the specification a case is being run under."
        ),
    )


class IngestedArtifact(BaseModel):
    """A tenant-scoped, hashed, versioned artifact reference.

    From here on this is the only thing that travels between nodes. The bytes
    stay in the artifact service.
    """

    tenant_id: str
    case_id: str
    batch_id: str
    artifact_filename: str
    artifact_version: int
    artifact_sha256: str
    mime_type: str
    byte_size: int
    page_count: int
    source_uri: str
    spec_id: str
    spec_version: str
    spec_sha256: str
    ingested_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    decision_authority: str = "none"


class ExtractedField(BaseModel):
    """A model reading joined to verified artifact provenance."""

    field_name: str
    value_text: str
    unit_text: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    anchor: SourceAnchor
    legibility_note: str = ""


class ExtractedEvidence(BaseModel):
    """Extractor output for one artifact, provenance-stamped, not yet judged."""

    tenant_id: str
    case_id: str
    batch_id: str
    artifact_filename: str
    artifact_version: int
    artifact_sha256: str
    page_count: int = Field(
        ge=1,
        description=(
            "Carried forward from the artifact, stamped by deterministic code. "
            "Validation bounds anchor page numbers against this rather than "
            "against anything the extractor asserted."
        ),
    )
    spec_id: str
    spec_version: str
    spec_sha256: str
    extractor_agent: str
    extractor_model: str
    fields: list[ExtractedField] = Field(default_factory=list)
    unreadable_field_names: list[str] = Field(default_factory=list)
    unspecified_field_names: list[str] = Field(
        default_factory=list,
        description=(
            "Field names the extractor returned that the approved spec does "
            "not contain. Dropped from the record and reported, never merged: "
            "an extraction stage must not be able to invent new controlled "
            "fields."
        ),
    )
    conflicting_field_values: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Specified field names the extractor read more than once with "
            "disagreeing value_text, mapped to every value_text it reported. "
            "Distinct from unreadable: the extractor saw the field and "
            "disagreed with itself, rather than seeing nothing."
        ),
    )
    extracted_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    decision_authority: str = "none"


class ValidatedField(BaseModel):
    """One field after deterministic parsing, spec checks and thresholding."""

    field_name: str
    label: str
    status: FieldStatus
    value_text: str | None = None
    typed_value: str | int | float | bool | None = Field(
        default=None,
        description=(
            "Value after deterministic parsing. None when the field is "
            "missing or failed to parse. Datetimes are ISO-8601 strings so "
            "the record round-trips through JSON unchanged."
        ),
    )
    unit: str | None = None
    confidence: float | None = None
    required_confidence: float | None = None
    findings: list[str] = Field(
        default_factory=list,
        description="Every check that failed, named, for audit.",
    )
    anchor: SourceAnchor | None = None
    legibility_note: str = ""


class EvidenceAuditEvent(BaseModel):
    """Immutable record that an extraction happened, and under what controls."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = "evidence_extraction_validated"
    tenant_id: str
    case_id: str
    batch_id: str
    artifact_filename: str
    artifact_version: int
    artifact_sha256: str
    spec_id: str
    spec_version: str
    spec_sha256: str
    extractor_agent: str
    extractor_model: str
    status: EvidenceStatus
    accepted_field_count: int
    review_field_count: int
    invalid_field_count: int
    missing_field_count: int
    review_queue: list[str] = Field(default_factory=list)
    recorded_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    decision_authority: str = "none"


class ValidatedEvidence(BaseModel):
    """Final output of the evidence-extraction stage.

    ``status`` is computed by deterministic code from per-field outcomes. It is
    an input to a human's review, not a disposition: nothing here releases a
    batch or closes an investigation.
    """

    tenant_id: str
    case_id: str
    batch_id: str
    artifact_filename: str
    artifact_version: int
    artifact_sha256: str
    spec_id: str
    spec_version: str
    spec_sha256: str
    extractor_agent: str
    extractor_model: str
    status: EvidenceStatus
    fields: list[ValidatedField] = Field(default_factory=list)
    record_findings: list[str] = Field(
        default_factory=list,
        description="Cross-field GMP checks that failed at record level.",
    )
    review_queue: list[str] = Field(
        default_factory=list,
        description=(
            "Fields a qualified human must verify against the source page "
            "before this evidence supports any conclusion."
        ),
    )
    audit: EvidenceAuditEvent
    validated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    decision_authority: str = "none"
