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

"""Deterministic validation, confidence routing and the review queue.

Everything here is pure functions over the extractor's output plus approved
master data: no model, no I/O, no clock beyond the record timestamp. That is
the point. A QP or an inspector can re-run this file against a stored
``ExtractedEvidence`` record and get byte-identical findings, which is what
makes the transcription reviewable rather than merely plausible.

Three layers of check, matching what the incumbent products actually do:

1. **Syntactic** — does the transcription parse into the specified type at all.
2. **Structural** — pattern, allowed values, unit, and the presence of a usable
   source anchor.
3. **GMP semantic** — acceptance ranges, chronology, and independent second-person
   verification.

Then confidence routing on top: a field can parse cleanly, sit inside its range,
and *still* require human verification because the extractor said it was hard to
read. MasterControl's position that confidence scoring must route low-confidence
extractions to a human is implemented literally, and critical fields carry the
higher bar.

Two design choices worth stating because they look like bugs and are not:

* **An out-of-range value is a finding, not an error.** Blender speed reading
  18 rpm against an 8-14 rpm range is very likely the actual deviation under
  investigation. It is marked ``invalid``, queued for verification, and its
  value is preserved. Discarding it would delete the evidence.
* **Nothing here decides anything.** ``EvidenceStatus`` says whether a human
  must look, not whether the batch is acceptable. No release logic, no severity
  change, no CAPA. ``decision_authority`` stays ``"none"``.
"""

from __future__ import annotations

import datetime
import re

from google.adk.workflow import node

from app.domain.evidence_models import (
    EvidenceAuditEvent,
    EvidenceStatus,
    ExtractedEvidence,
    ExtractedField,
    FieldStatus,
    ValidatedEvidence,
    ValidatedField,
)
from app.domain.master_data import (
    DocumentFieldSpec,
    FieldKind,
    FieldSpec,
    load_document_field_spec,
)

_TRUE_TOKENS = frozenset({"yes", "y", "true", "t", "1", "x", "checked"})
_FALSE_TOKENS = frozenset({"no", "n", "false", "f", "0", "none", "unchecked"})

# Only unambiguous separators are accepted in a transcribed number. A comma is
# deliberately not treated as a thousands separator: "1,5" is one and a half in
# much of the world and fifteen in none of it, and guessing on a GMP record is
# how a data-integrity finding is written.
_NUMBER_PATTERN = re.compile(r"^[+-]?(\d+(\.\d+)?|\.\d+)$")


def _parse_number(text: str) -> float | None:
    cleaned = text.strip().replace(" ", "")
    if not _NUMBER_PATTERN.match(cleaned):
        return None
    return float(cleaned)


def _parse_datetime(text: str) -> datetime.datetime | None:
    """Parses a timestamp from a controlled record.

    ISO-8601 only, plus the ``YYYY-MM-DD HH:MM`` form that appears on printed
    batch records. Free-form dates are rejected rather than guessed: ``03/04``
    is ambiguous between two valid readings, and on a GMP record an incorrect
    chronology is a finding in its own right.
    """
    candidate = text.strip()
    if not candidate:
        return None
    try:
        return datetime.datetime.fromisoformat(candidate)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _parse_boolean(text: str) -> bool | None:
    token = text.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return None


def _validate_anchor(field: ExtractedField, page_count: int) -> list[str]:
    """Checks that the reading points somewhere a reviewer can actually go."""
    findings: list[str] = []
    anchor = field.anchor
    if anchor.page_number > page_count:
        findings.append(
            f"anchor_page_out_of_range: cited page {anchor.page_number} "
            f"exceeds the {page_count}-page artifact"
        )
    box = anchor.bounding_box
    if box is not None and not box.is_well_formed:
        findings.append("anchor_bounding_box_degenerate: box encloses no area")
    if box is None and not anchor.transcribed_context:
        findings.append(
            "anchor_unlocatable: no bounding box and no transcribed context, "
            "so the reading cannot be located on the page for verification"
        )
    return findings


def _validate_value(
    spec: FieldSpec, field: ExtractedField
) -> tuple[str | int | float | bool | None, list[str]]:
    """Parses and checks one transcription against its field specification."""
    findings: list[str] = []
    text = field.value_text.strip()

    if not text:
        return None, ["empty_transcription: extractor returned a blank value"]

    if spec.unit is not None:
        if field.unit_text is None:
            findings.append(
                f"unit_absent: spec requires the value to be reported in "
                f"{spec.unit!r} and no unit was printed"
            )
        elif field.unit_text.strip() != spec.unit:
            findings.append(
                f"unit_mismatch: read {field.unit_text.strip()!r}, spec "
                f"requires {spec.unit!r}; values are never silently converted"
            )
    elif field.unit_text:
        findings.append(
            f"unexpected_unit: read unit {field.unit_text.strip()!r} on a "
            "field the spec declares unitless"
        )

    typed: str | int | float | bool | None
    if spec.kind is FieldKind.STRING:
        typed = text
    elif spec.kind is FieldKind.INTEGER:
        number = _parse_number(text)
        if number is None or number != int(number):
            return None, [*findings, f"parse_failed: {text!r} is not an integer"]
        typed = int(number)
    elif spec.kind is FieldKind.NUMBER:
        number = _parse_number(text)
        if number is None:
            return None, [*findings, f"parse_failed: {text!r} is not a number"]
        typed = number
    elif spec.kind is FieldKind.BOOLEAN:
        flag = _parse_boolean(text)
        if flag is None:
            return None, [
                *findings,
                f"parse_failed: {text!r} is not an unambiguous yes/no",
            ]
        typed = flag
    else:  # FieldKind.DATETIME
        moment = _parse_datetime(text)
        if moment is None:
            return None, [
                *findings,
                f"parse_failed: {text!r} is not an unambiguous timestamp",
            ]
        typed = moment.isoformat()

    if spec.pattern is not None and isinstance(typed, str):
        if not re.match(spec.pattern, typed):
            findings.append(
                f"pattern_mismatch: {typed!r} does not match the specified "
                f"format {spec.pattern!r}"
            )
    if spec.allowed_values is not None and isinstance(typed, str):
        if typed not in spec.allowed_values:
            findings.append(
                f"value_not_permitted: {typed!r} is not one of "
                f"{list(spec.allowed_values)}"
            )
    if isinstance(typed, (int, float)) and not isinstance(typed, bool):
        if spec.minimum is not None and typed < spec.minimum:
            findings.append(
                f"below_specification: {typed} is under the minimum "
                f"{spec.minimum}{' ' + spec.unit if spec.unit else ''}"
            )
        if spec.maximum is not None and typed > spec.maximum:
            findings.append(
                f"above_specification: {typed} is over the maximum "
                f"{spec.maximum}{' ' + spec.unit if spec.unit else ''}"
            )

    return typed, findings


def validate_field(
    document_spec: DocumentFieldSpec,
    field_spec: FieldSpec,
    field: ExtractedField,
    page_count: int,
) -> ValidatedField:
    """Validates one transcribed field and assigns its status."""
    typed, findings = _validate_value(field_spec, field)
    findings = [*findings, *_validate_anchor(field, page_count)]
    required_confidence = document_spec.min_confidence_for(field_spec)

    if typed is None or findings:
        status = FieldStatus.INVALID
    elif field.confidence < required_confidence:
        status = FieldStatus.REVIEW_REQUIRED
        findings = [
            *findings,
            f"below_confidence_threshold: {field.confidence:.2f} is under the "
            f"{'critical' if field_spec.critical else 'standard'} threshold of "
            f"{required_confidence:.2f}",
        ]
    else:
        status = FieldStatus.ACCEPTED

    return ValidatedField(
        field_name=field.field_name,
        label=field_spec.label,
        status=status,
        value_text=field.value_text,
        typed_value=typed,
        unit=field.unit_text,
        confidence=field.confidence,
        required_confidence=required_confidence,
        findings=findings,
        anchor=field.anchor,
        legibility_note=field.legibility_note,
    )


def _missing_field(
    document_spec: DocumentFieldSpec,
    field_spec: FieldSpec,
    reason: str,
) -> ValidatedField:
    return ValidatedField(
        field_name=field_spec.field_name,
        label=field_spec.label,
        status=FieldStatus.MISSING,
        required_confidence=document_spec.min_confidence_for(field_spec),
        findings=[reason],
    )


def _accepted_values(fields: list[ValidatedField]) -> dict[str, object]:
    """Values usable for cross-field checks.

    Fields that failed to parse are excluded: a chronology check against a
    value that could not be read would produce a second, misleading finding on
    top of the real one.
    """
    return {
        field.field_name: field.typed_value
        for field in fields
        if field.typed_value is not None
        and field.status is not FieldStatus.MISSING
    }


def apply_record_level_checks(fields: list[ValidatedField]) -> list[str]:
    """Cross-field GMP checks that no single field can express.

    These are page-level record integrity rules, not process judgements:

    * A step cannot end before it starts.
    * A recorded blend duration must agree with the recorded start and end
      times. Two independent records of the same fact that disagree is a data
      integrity finding, which is why both are transcribed rather than one
      being computed from the other.
    * Performed-by and verified-by initials must differ. Independent
      second-person verification is a foundational GMP control; the same person
      signing both lines defeats it, and it is the kind of thing that is easy to
      miss in a 400-page record and impossible to argue with once flagged.
    """
    findings: list[str] = []
    values = _accepted_values(fields)

    start_raw = values.get("step_start_time")
    end_raw = values.get("step_end_time")
    start = end = None
    if isinstance(start_raw, str) and isinstance(end_raw, str):
        start = _parse_datetime(start_raw)
        end = _parse_datetime(end_raw)
        if start and end and end <= start:
            findings.append(
                f"chronology_invalid: step_end_time {end_raw} is not after "
                f"step_start_time {start_raw}"
            )

    duration = values.get("blend_time_min")
    if (
        start is not None
        and end is not None
        and end > start
        and isinstance(duration, (int, float))
        and not isinstance(duration, bool)
    ):
        elapsed = (end - start).total_seconds() / 60.0
        if abs(elapsed - float(duration)) > 1.0:
            findings.append(
                f"duration_inconsistent: recorded blend_time_min {duration} "
                f"disagrees with the {elapsed:.1f} min implied by the recorded "
                "start and end times"
            )

    operator = values.get("operator_initials")
    verifier = values.get("verifier_initials")
    if (
        isinstance(operator, str)
        and isinstance(verifier, str)
        and operator.strip().upper() == verifier.strip().upper()
    ):
        findings.append(
            f"second_person_verification_absent: performed-by and verified-by "
            f"initials are both {operator.strip().upper()!r}"
        )

    return findings


def validate_extracted_evidence(
    evidence: ExtractedEvidence,
    page_count: int,
    document_spec: DocumentFieldSpec | None = None,
) -> ValidatedEvidence:
    """Validates a whole extracted page and builds its review queue and audit event.

    Args:
        evidence: Provenance-stamped extractor output.
        page_count: Pages in the source artifact, used to bound anchor pages.
        document_spec: Approved spec to validate against. Defaults to the spec
            in force; an explicit spec is accepted so a case can be re-validated
            under the specification it was originally run against.

    Returns:
        A ``ValidatedEvidence`` record. ``status`` is
        ``HUMAN_REVIEW_REQUIRED`` whenever any specified field is missing,
        invalid, or below its confidence threshold, or when any record-level
        check fails.

    Raises:
        ValueError: If the record was extracted under a different specification
            than the one it is being validated against. Validating a
            transcription against a spec that was not in force when it was
            produced would make the resulting findings unattributable.
    """
    spec = document_spec or load_document_field_spec()
    if evidence.spec_sha256 != spec.spec_sha256:
        raise ValueError(
            f"Evidence was extracted under spec {evidence.spec_version} "
            f"({evidence.spec_sha256[:12]}) but is being validated against "
            f"{spec.spec_version} ({spec.spec_sha256[:12]})."
        )
    if evidence.spec_id != spec.spec_id:
        # Defensive, beyond the hash check above: a spec-id mismatch masked by
        # a coincidental hash match is not something SHA-256 alone rules out
        # if a second document type is ever introduced under this pipeline.
        raise ValueError(
            f"Evidence was extracted under spec id {evidence.spec_id!r} but is "
            f"being validated against {spec.spec_id!r}."
        )

    by_name = {field.field_name: field for field in evidence.fields}
    unreadable = set(evidence.unreadable_field_names)
    validated: list[ValidatedField] = []

    for field_spec in spec.fields:
        field = by_name.get(field_spec.field_name)
        conflicting_values = evidence.conflicting_field_values.get(
            field_spec.field_name
        )
        if field is not None:
            validated.append(
                validate_field(spec, field_spec, field, page_count)
            )
        elif conflicting_values:
            validated.append(
                _missing_field(
                    spec,
                    field_spec,
                    "conflicting_transcription: extractor read this field "
                    f"more than once with disagreeing values {conflicting_values!r}; "
                    "a human must resolve which, if any, is correct",
                )
            )
        elif field_spec.field_name in unreadable:
            validated.append(
                _missing_field(
                    spec,
                    field_spec,
                    "reported_unreadable: extractor could not read this field "
                    "and correctly declined to guess",
                )
            )
        elif field_spec.required:
            validated.append(
                _missing_field(
                    spec,
                    field_spec,
                    "required_field_absent: not returned by the extractor",
                )
            )

    record_findings = apply_record_level_checks(validated)
    batch_field = next(
        (field for field in validated if field.field_name == "batch_number"), None
    )
    if (
        batch_field is not None
        and batch_field.status is FieldStatus.ACCEPTED
        and batch_field.typed_value != evidence.batch_id
    ):
        record_findings.append(
            "batch_scope_mismatch: extracted batch number "
            f"{batch_field.typed_value!r} does not match case batch "
            f"{evidence.batch_id!r}"
        )
    for name in evidence.unspecified_field_names:
        record_findings.append(
            f"unspecified_field_discarded: extractor returned {name!r}, which "
            "the approved specification does not contain"
        )

    review_queue = [
        field.field_name
        for field in validated
        if field.status is not FieldStatus.ACCEPTED
    ]
    status = (
        EvidenceStatus.ACCEPTED
        if not review_queue and not record_findings
        else EvidenceStatus.HUMAN_REVIEW_REQUIRED
    )

    def _count(target: FieldStatus) -> int:
        return sum(1 for field in validated if field.status is target)

    audit = EvidenceAuditEvent(
        tenant_id=evidence.tenant_id,
        case_id=evidence.case_id,
        batch_id=evidence.batch_id,
        artifact_filename=evidence.artifact_filename,
        artifact_version=evidence.artifact_version,
        artifact_sha256=evidence.artifact_sha256,
        spec_id=evidence.spec_id,
        spec_version=evidence.spec_version,
        spec_sha256=evidence.spec_sha256,
        extractor_agent=evidence.extractor_agent,
        extractor_model=evidence.extractor_model,
        status=status,
        accepted_field_count=_count(FieldStatus.ACCEPTED),
        review_field_count=_count(FieldStatus.REVIEW_REQUIRED),
        invalid_field_count=_count(FieldStatus.INVALID),
        missing_field_count=_count(FieldStatus.MISSING),
        review_queue=review_queue,
    )

    return ValidatedEvidence(
        tenant_id=evidence.tenant_id,
        case_id=evidence.case_id,
        batch_id=evidence.batch_id,
        artifact_filename=evidence.artifact_filename,
        artifact_version=evidence.artifact_version,
        artifact_sha256=evidence.artifact_sha256,
        spec_id=evidence.spec_id,
        spec_version=evidence.spec_version,
        spec_sha256=evidence.spec_sha256,
        extractor_agent=evidence.extractor_agent,
        extractor_model=evidence.extractor_model,
        status=status,
        fields=validated,
        record_findings=record_findings,
        review_queue=review_queue,
        audit=audit,
    )


@node(name="validate_page_evidence")
def validate_page_evidence(node_input: ExtractedEvidence) -> ValidatedEvidence:
    """Validates transcribed evidence and routes low-confidence fields to a human.

    The page bound used to check anchors comes from
    ``ExtractedEvidence.page_count``, which deterministic code carried forward
    from the ingested artifact — not from anything the extractor asserted. So a
    reading that cites page 4 of a one-page record is a finding, and cannot be
    made to look valid by the model claiming a larger document.

    Args:
        node_input: Provenance-stamped extractor output.

    Returns:
        A ``ValidatedEvidence`` record carrying per-field findings, the review
        queue, and an immutable audit event.
    """
    return validate_extracted_evidence(node_input, page_count=node_input.page_count)
