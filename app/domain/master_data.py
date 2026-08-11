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

"""Versioned, change-controlled document field specifications.

What may be extracted from a GMP record, the unit it must be reported in, the
acceptance range, and the confidence a transcription must reach are **master
data**, not prompt text. They live in a versioned JSON file carrying its own
``spec_version``, ``approved_by`` and ``change_control_ref``, and every
extraction result records the ``spec_version`` *and the SHA-256 of the exact
spec bytes* it was produced under. An auditor can therefore prove which
approved specification governed a given transcription, and can detect a spec
that changed without going through change control.

This follows the invoice-processing ADK sample's versioned master-data loader
pattern (``shared_libraries/master_data_loader.py``) but keeps only the parts
that suit GxP: load, pin, hash, and expose read-only. There is no runtime
mutation path — a spec change is a file change, reviewed and approved like any
other controlled document.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import pathlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_logger = logging.getLogger(__name__)

_MASTER_DATA_DIR = pathlib.Path(__file__).parent / "master_data"

BMR_PAGE_SPEC_FILENAME = "bmr_page_spec_v1.json"


class FieldKind(StrEnum):
    """Value domain a specified field must parse into.

    Deterministic code — never the model — performs the parse. The extractor
    returns the characters it read; this enum decides what those characters are
    allowed to become.
    """

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"


class FieldSpec(BaseModel):
    """Specification for one extractable field on a controlled document."""

    model_config = ConfigDict(frozen=True)

    field_name: str
    label: str
    kind: FieldKind
    required: bool
    critical: bool = Field(
        description=(
            "Critical fields carry the higher confidence bar and are the fields "
            "a QP is most likely to be challenged on. Marking a field critical "
            "does not give an agent authority over it — it raises the bar for "
            "accepting a transcription without human verification."
        )
    )
    unit: str | None = Field(
        default=None,
        description=(
            "Expected unit, verbatim. A transcription reporting a different "
            "unit is a validation failure, never a silent conversion: unit "
            "coercion inside an extraction step is exactly how a GMP data "
            "integrity finding is created."
        ),
    )
    minimum: float | None = None
    maximum: float | None = None
    allowed_values: tuple[str, ...] | None = None
    pattern: str | None = None


class DocumentFieldSpec(BaseModel):
    """A whole approved document specification, plus its content hash."""

    model_config = ConfigDict(frozen=True)

    spec_id: str
    spec_version: str
    spec_sha256: str = Field(
        description="SHA-256 of the spec file bytes this object was loaded from."
    )
    effective_date: str
    approved_by: str
    change_control_ref: str
    description: str
    critical_field_min_confidence: float = Field(ge=0.0, le=1.0)
    standard_field_min_confidence: float = Field(ge=0.0, le=1.0)
    fields: tuple[FieldSpec, ...]

    def field(self, field_name: str) -> FieldSpec | None:
        """Returns the spec for ``field_name``, or None if it is not specified."""
        for spec in self.fields:
            if spec.field_name == field_name:
                return spec
        return None

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(spec.field_name for spec in self.fields)

    @property
    def required_field_names(self) -> tuple[str, ...]:
        return tuple(spec.field_name for spec in self.fields if spec.required)

    def min_confidence_for(self, spec: FieldSpec) -> float:
        """Confidence a transcription of ``spec`` must reach to be accepted."""
        return (
            self.critical_field_min_confidence
            if spec.critical
            else self.standard_field_min_confidence
        )


@functools.cache
def load_document_field_spec(filename: str = BMR_PAGE_SPEC_FILENAME) -> DocumentFieldSpec:
    """Loads and pins an approved document field specification.

    Cached because the spec is immutable for the life of the process: a
    deployed runtime must not observe a specification changing underneath a
    case that is already in flight. Picking up a change-controlled spec update
    requires a process restart; the loaded version and hash are logged so an
    operator can observe which specification a running instance is actually
    serving, rather than inferring the cutover from behaviour.

    Raises:
        FileNotFoundError: If the named spec is not present in the package.
        ValueError: If the spec is malformed or declares duplicate fields.
    """
    path = _MASTER_DATA_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Document field spec not found: {filename}")

    raw = path.read_bytes()
    payload = json.loads(raw)
    payload["spec_sha256"] = hashlib.sha256(raw).hexdigest()

    spec = DocumentFieldSpec.model_validate(payload)

    names = [field.field_name for field in spec.fields]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(
            f"Spec {filename} declares duplicate field names: {duplicates}"
        )
    if not spec.fields:
        raise ValueError(f"Spec {filename} declares no fields.")

    _logger.info(
        "Loaded document field spec %s: version=%s sha256=%s change_control_ref=%s",
        spec.spec_id,
        spec.spec_version,
        spec.spec_sha256,
        spec.change_control_ref,
    )
    return spec
