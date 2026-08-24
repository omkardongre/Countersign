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
CFI_TRIGGER_SPEC_FILENAME = "cfi_trigger_spec_v1.json"
CFI_BUDGET_SPEC_FILENAME = "cfi_budget_spec_v1.json"


class RootCauseCategory(StrEnum):
    """Ishikawa/fishbone root-cause dimension a field's deviation would sit in.

    Katalyze's published Deviation Investigator agent frames investigation
    around **material, machine, method and measure**
    (``final_reseach/01_katalyze_ai.md`` section 3). That four-category set is
    a subset of the standard 6M Ishikawa fishbone (man/machine/material/
    method/measurement/environment) used across quality engineering generally,
    not something specific to any vendor, so ``PERSONNEL`` and ``ENVIRONMENT``
    are kept alongside Katalyze's four rather than dropped to match it exactly.

    This tag is master data, not something the compliance engine infers: which
    dimension a field's failure implicates is a quality-engineering judgement
    made once, at spec-approval time, the same way the field's unit and range
    are. It exists so a compliance finding can hand the (future) Investigation
    agent a starting hypothesis category instead of a bare field name.
    """

    MATERIAL = "material"
    MACHINE = "machine"
    METHOD = "method"
    MEASUREMENT = "measurement"
    PERSONNEL = "personnel"
    ENVIRONMENT = "environment"
    UNCLASSIFIED = "unclassified"
    """Identifiers and metadata (batch number, page number) that are not
    themselves a process parameter and so do not implicate a root-cause
    dimension when they fail a check."""


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
    root_cause_category: RootCauseCategory = Field(
        default=RootCauseCategory.UNCLASSIFIED,
        description=(
            "Fishbone dimension this field's deviation would implicate. "
            "Defaults to unclassified for specs written before this field "
            "existed, so older approved specs keep loading rather than "
            "failing validation."
        ),
    )
    trend_tracked: bool = Field(
        default=False,
        description=(
            "Whether historical-trend (OOT) comparison applies to this "
            "field. Only numeric process parameters with an approved trend "
            "baseline should set this — an identifier or a timestamp has no "
            "trend to be out of."
        ),
    )


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


class TrendFieldBaseline(BaseModel):
    """Historical mean/standard-deviation baseline for one trended field."""

    model_config = ConfigDict(frozen=True)

    field_name: str
    unit: str | None = None
    trend_mean: float
    trend_std_dev: float = Field(
        gt=0.0,
        description=(
            "Must be strictly positive: a zero-variance baseline would make "
            "every deviation from the mean, however small, register as an "
            "infinite-sigma OOT trigger."
        ),
    )


class TrendBaseline(BaseModel):
    """An approved historical-trend baseline for one product's process step.

    Separate master data from ``DocumentFieldSpec``, on its own change-control
    cadence: a field's acceptance *range* is a specification decision, while
    its trend *baseline* is a recalculated statistical fact about how the
    process has actually been running. Recomputing the baseline from a new
    batch window must not require re-approving the specification, and vice
    versa — USP <1010> and the OOT literature both treat trend analysis as
    distinct from specification conformance (a result can be in-spec and
    still out-of-trend).
    """

    model_config = ConfigDict(frozen=True)

    baseline_id: str
    baseline_version: str
    baseline_sha256: str
    product_code: str
    effective_date: str
    approved_by: str
    change_control_ref: str
    description: str
    method: str = Field(
        description=(
            "How the baseline was computed. Only 'mean_stdev' is currently "
            "consumed by the compliance engine; other values load but are "
            "not evaluated, so a future method cannot be silently misread as "
            "sigma statistics."
        )
    )
    sigma_threshold: float = Field(
        gt=0.0,
        description="Sigma distance from trend_mean that triggers an OOT finding.",
    )
    sample_batch_count: int = Field(ge=1)
    baseline_period_start: str
    baseline_period_end: str
    fields: tuple[TrendFieldBaseline, ...]

    def field(self, field_name: str) -> TrendFieldBaseline | None:
        for baseline in self.fields:
            if baseline.field_name == field_name:
                return baseline
        return None


@functools.cache
def load_trend_baseline(filename: str) -> TrendBaseline:
    """Loads and pins an approved historical-trend baseline.

    Unlike the document field spec, there is no single default filename: a
    trend baseline is specific to one product, so callers pass the filename
    for the product/step they are validating. Cached for the same reason as
    ``load_document_field_spec`` — a case in flight must not observe its
    baseline changing mid-investigation.

    Raises:
        FileNotFoundError: If the named baseline is not present.
        ValueError: If the baseline is malformed, uses an unrecognised
            ``method``, or declares duplicate field names.
    """
    path = _MASTER_DATA_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Trend baseline not found: {filename}")

    raw = path.read_bytes()
    payload = json.loads(raw)
    payload["baseline_sha256"] = hashlib.sha256(raw).hexdigest()

    baseline = TrendBaseline.model_validate(payload)

    names = [field.field_name for field in baseline.fields]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(
            f"Baseline {filename} declares duplicate field names: {duplicates}"
        )

    _logger.info(
        "Loaded trend baseline %s: version=%s sha256=%s product=%s "
        "change_control_ref=%s",
        baseline.baseline_id,
        baseline.baseline_version,
        baseline.baseline_sha256,
        baseline.product_code,
        baseline.change_control_ref,
    )
    return baseline


class CfiTriggerSpec(BaseModel):
    """Change-controlled thresholds for the deterministic CFI trigger.

    ``decide_cfi_required`` (``app/domain/cfi_trigger.py``) is a pure
    function over these values and the joined case. Neither the trigger
    function nor any agent decides whether a CFI is warranted from a
    literal in code — the threshold itself is master data, approved and
    hashed like every other spec in this module, per GMPSOP QMS-130
    section 4.2.1 (a human/rule call, not an investigator's).
    """

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
    low_confidence_threshold: float = Field(ge=0.0, le=1.0)
    recurrence_prior_occurrences_threshold: int = Field(ge=0)


class CfiBudgetSpec(BaseModel):
    """Change-controlled bounded-autonomy caps for the CFI planning loop.

    Enforced by ``cfi_continue_gate`` (``app/domain/cfi_gate.py``), never by
    a prompt. ``max_wall_clock_days`` mirrors GMPSOP QMS-130's own 90-day
    open-CFI review trigger.
    """

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
    max_planning_rounds: int = Field(ge=1)
    max_tool_calls_per_round: int = Field(ge=1)
    max_wall_clock_days: int = Field(ge=1)


@functools.cache
def load_cfi_trigger_spec(filename: str = CFI_TRIGGER_SPEC_FILENAME) -> CfiTriggerSpec:
    """Loads and pins the approved CFI trigger threshold specification.

    Cached for the same reason as ``load_document_field_spec``: a CFI
    trigger decision made mid-deployment must not silently shift underneath
    cases already in flight because a file changed on disk without a
    process restart.

    Raises:
        FileNotFoundError: If the named spec is not present.
        ValueError: If the spec is malformed.
    """
    path = _MASTER_DATA_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"CFI trigger spec not found: {filename}")

    raw = path.read_bytes()
    payload = json.loads(raw)
    payload["spec_sha256"] = hashlib.sha256(raw).hexdigest()

    spec = CfiTriggerSpec.model_validate(payload)

    _logger.info(
        "Loaded CFI trigger spec %s: version=%s sha256=%s change_control_ref=%s",
        spec.spec_id,
        spec.spec_version,
        spec.spec_sha256,
        spec.change_control_ref,
    )
    return spec


@functools.cache
def load_cfi_budget_spec(filename: str = CFI_BUDGET_SPEC_FILENAME) -> CfiBudgetSpec:
    """Loads and pins the approved CFI bounded-autonomy budget specification.

    Cached for the same reason as ``load_cfi_trigger_spec``.

    Raises:
        FileNotFoundError: If the named spec is not present.
        ValueError: If the spec is malformed.
    """
    path = _MASTER_DATA_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"CFI budget spec not found: {filename}")

    raw = path.read_bytes()
    payload = json.loads(raw)
    payload["spec_sha256"] = hashlib.sha256(raw).hexdigest()

    spec = CfiBudgetSpec.model_validate(payload)

    _logger.info(
        "Loaded CFI budget spec %s: version=%s sha256=%s change_control_ref=%s",
        spec.spec_id,
        spec.spec_version,
        spec.spec_sha256,
        spec.change_control_ref,
    )
    return spec


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
