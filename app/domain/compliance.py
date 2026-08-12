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

"""The deterministic GMP compliance engine.

Pure functions, no model call, no I/O beyond reading pinned master data — same
posture as ``app.domain.classification`` and ``app.domain.evidence_validation``.
This is the node the research called for: three separable checks
(completeness, OOS, OOT), each citing its source, none of them deciding
anything.

Why three checks and not one blended "is this evidence okay" verdict: FDA's OOS
guidance, USP <1010>'s OOT treatment, and ICH Q7's batch-record-review
expectations are three distinct regulatory questions with different triggers
and different consequences. Collapsing them into one status would lose exactly
the information a QP needs to route the case correctly — a completeness gap
sends someone back to the record, an OOS finding opens a Phase I/II
investigation, an OOT finding starts a trend review, and a batch can have all
three, or one, or none, independently of the others.
"""

from __future__ import annotations

from google.adk.workflow import node

from app.domain.compliance_models import (
    ComplianceAuditEvent,
    ComplianceFinding,
    ComplianceFindingType,
    ComplianceResult,
    ComplianceStatus,
    OosPhase,
)
from app.domain.evidence_models import FieldStatus, ValidatedEvidence
from app.domain.master_data import (
    DocumentFieldSpec,
    FieldSpec,
    RootCauseCategory,
    TrendBaseline,
    load_document_field_spec,
    load_trend_baseline,
)


def check_completeness(
    spec: DocumentFieldSpec, evidence: ValidatedEvidence
) -> list[ComplianceFinding]:
    """Checks that every required field is present and settled.

    ICH Q7 / batch record review requires demonstrated completeness — every
    required entry present and verifiable — before a record can support any
    downstream disposition step. A field that is present but still
    ``review_required`` (a human has not yet verified a low-confidence
    transcription) counts as incomplete for this purpose: an unverified
    reading is not yet part of the record.

    Args:
        spec: The approved field specification the evidence was validated
            against.
        evidence: The validated evidence to check.

    Returns:
        One ``ComplianceFinding`` per required field that is missing or still
        unverified. Empty when the record is complete.
    """
    by_name = {field.field_name: field for field in evidence.fields}
    findings: list[ComplianceFinding] = []

    for field_spec in spec.fields:
        if not field_spec.required:
            continue
        field = by_name.get(field_spec.field_name)
        if field is None or field.status is FieldStatus.MISSING:
            findings.append(
                ComplianceFinding(
                    finding_type=ComplianceFindingType.COMPLETENESS,
                    field_name=field_spec.field_name,
                    label=field_spec.label,
                    root_cause_category=field_spec.root_cause_category,
                    description=(
                        f"{field_spec.label} is required by "
                        f"{spec.spec_id} v{spec.spec_version} and is not "
                        "present as a settled reading in this evidence record."
                    ),
                    anchor=field.anchor if field else None,
                )
            )
        elif field.status is FieldStatus.REVIEW_REQUIRED:
            findings.append(
                ComplianceFinding(
                    finding_type=ComplianceFindingType.COMPLETENESS,
                    field_name=field_spec.field_name,
                    label=field_spec.label,
                    root_cause_category=field_spec.root_cause_category,
                    description=(
                        f"{field_spec.label} is present but has not cleared "
                        "human confidence review; an unverified reading does "
                        "not count toward record completeness."
                    ),
                    anchor=field.anchor,
                    observed_value=field.value_text,
                )
            )

    return findings


def _describe_range(field_spec: FieldSpec) -> str | None:
    if field_spec.minimum is None and field_spec.maximum is None:
        return None
    unit = f" {field_spec.unit}" if field_spec.unit else ""
    if field_spec.minimum is not None and field_spec.maximum is not None:
        return f"{field_spec.minimum}-{field_spec.maximum}{unit}"
    if field_spec.minimum is not None:
        return f">= {field_spec.minimum}{unit}"
    return f"<= {field_spec.maximum}{unit}"


# Finding-name prefixes, as emitted by ``evidence_validation``, that mean the
# value parsed cleanly and was measured against its approved range or
# baseline — a genuine range violation with no assignable transcription cause
# on record. Every other finding name (parse failures, pattern/unit/allowed-
# value mismatches, unlocatable or malformed anchors, an empty transcription)
# means something about *reading* the field was already wrong, which is an
# assignable cause in FDA OOS terms and starts the finding past Phase I.
#
# Deliberately an allow-list, not a deny-list: a new finding name added to
# evidence_validation in the future and not added here falls safely to
# Phase II (assignable cause on record) rather than silently being treated as
# a clean range violation that skips Phase I scrutiny it may not deserve.
_RANGE_ONLY_FINDING_PREFIXES = ("below_specification", "above_specification")


def _oos_phase_for(field_findings: list[str]) -> OosPhase:
    """Assigns the OOS phase gate from a field's validation findings.

    Phase I (no assignable cause yet) only when *every* finding on the field
    is a pure range violation. A field carrying a range finding alongside an
    anchor or unit finding is not cleanly Phase-I-eligible either — the
    presence of any non-range finding means something about how the value was
    read or located is already suspect, so the whole field goes to Phase II.
    """
    if field_findings and all(
        finding.startswith(_RANGE_ONLY_FINDING_PREFIXES) for finding in field_findings
    ):
        return OosPhase.PHASE_I_LABORATORY
    return OosPhase.PHASE_II_FULL_SCALE


def check_specification_conformance(
    spec: DocumentFieldSpec, evidence: ValidatedEvidence
) -> list[ComplianceFinding]:
    """Flags fields validation already found invalid — the OOS trigger.

    This function does not re-derive out-of-range values; ``evidence_validation``
    already performed that arithmetic and recorded it in ``ValidatedField``.
    What this function adds is the regulatory framing FDA's OOS guidance
    requires: every invalid, settled numeric/measured field becomes a formal
    OOS finding with a phase assignment, so it is visible as *that specific
    regulatory category* rather than merely one more validation error among
    unit mismatches and pattern failures.

    Phase assignment (see ``_oos_phase_for``): a field whose findings are
    exclusively range violations starts at Phase I — no assignable cause is on
    record yet, and per the Barr Laboratories precedent, retesting must not
    occur before a formal investigation is opened. Any other finding on the
    field (parse, pattern, unit, allowed-value, or anchor/locatability
    problems) means an assignable transcription cause is already on record, so
    the field starts at Phase II.

    Args:
        spec: The approved field specification.
        evidence: The validated evidence to check.

    Returns:
        One ``ComplianceFinding`` per settled field with a range/pattern/
        allowed-value/unit violation. Fields still ``review_required`` or
        ``missing`` are excluded — those are completeness findings, not OOS
        findings, until a human settles the reading.
    """
    by_field_name = {field.field_name for field in spec.fields}
    findings: list[ComplianceFinding] = []

    for field in evidence.fields:
        if field.status is not FieldStatus.INVALID:
            continue
        if field.field_name not in by_field_name:
            continue
        field_spec = spec.field(field.field_name)
        assert field_spec is not None  # guaranteed by the membership check above

        phase = _oos_phase_for(field.findings)

        findings.append(
            ComplianceFinding(
                finding_type=ComplianceFindingType.OUT_OF_SPECIFICATION,
                field_name=field.field_name,
                label=field_spec.label,
                root_cause_category=field_spec.root_cause_category,
                description=(
                    f"{field_spec.label} failed specification conformance: "
                    f"{'; '.join(field.findings)}"
                ),
                anchor=field.anchor,
                observed_value=field.value_text,
                expected_value=_describe_range(field_spec),
                oos_phase=phase,
            )
        )

    return findings


def check_trend_conformance(
    spec: DocumentFieldSpec,
    evidence: ValidatedEvidence,
    baseline: TrendBaseline,
) -> list[ComplianceFinding]:
    """Flags settled, in-specification values that are still out-of-trend.

    USP <1010>'s treatment of OOT and the pharmaceutical OOT-investigation
    literature are explicit that this is a comparison against historical
    process data, not the specification limit — a value can pass
    ``check_specification_conformance`` and still trigger here. Only fields
    the approved spec marks ``trend_tracked`` are evaluated, and only when
    validation settled them as ``accepted`` (a value that failed spec
    conformance is already an OOS finding, not an OOT one; a value still
    pending human review has no settled number to trend-check yet).

    Args:
        spec: The approved field specification.
        evidence: The validated evidence to check.
        baseline: The approved historical-trend baseline for this product's
            process step. Must be for the ``method="mean_stdev"`` computation;
            other methods load but are not evaluated by this function.

    Returns:
        One ``ComplianceFinding`` per accepted, trend-tracked field whose
        value sits more than ``baseline.sigma_threshold`` standard deviations
        from the baseline mean.
    """
    if baseline.method != "mean_stdev":
        return []

    findings: list[ComplianceFinding] = []

    for field in evidence.fields:
        if field.status is not FieldStatus.ACCEPTED:
            continue
        field_spec = spec.field(field.field_name)
        if field_spec is None or not field_spec.trend_tracked:
            continue
        field_baseline = baseline.field(field.field_name)
        if field_baseline is None:
            continue
        if not isinstance(field.typed_value, (int, float)) or isinstance(
            field.typed_value, bool
        ):
            continue

        sigma_distance = (
            abs(field.typed_value - field_baseline.trend_mean)
            / field_baseline.trend_std_dev
        )
        if sigma_distance <= baseline.sigma_threshold:
            continue

        unit = f" {field_baseline.unit}" if field_baseline.unit else ""
        findings.append(
            ComplianceFinding(
                finding_type=ComplianceFindingType.OUT_OF_TREND,
                field_name=field.field_name,
                label=field_spec.label,
                root_cause_category=field_spec.root_cause_category,
                description=(
                    f"{field_spec.label} is within specification but "
                    f"{sigma_distance:.2f} standard deviations from the "
                    f"{baseline.sample_batch_count}-batch trend baseline mean "
                    f"of {field_baseline.trend_mean}{unit}, exceeding the "
                    f"{baseline.sigma_threshold}-sigma threshold."
                ),
                anchor=field.anchor,
                observed_value=field.value_text,
                expected_value=(
                    f"{field_baseline.trend_mean} +/- "
                    f"{baseline.sigma_threshold}*{field_baseline.trend_std_dev}{unit}"
                ),
                sigma_distance=sigma_distance,
            )
        )

    return findings


def _root_cause_summary(
    findings: list[ComplianceFinding],
) -> dict[RootCauseCategory, int]:
    summary: dict[RootCauseCategory, int] = {}
    for finding in findings:
        summary[finding.root_cause_category] = (
            summary.get(finding.root_cause_category, 0) + 1
        )
    return summary


def _extracted_product_code(evidence: ValidatedEvidence) -> str | None:
    """Returns the evidence's own settled, accepted ``product_code`` value.

    Read once, from the evidence itself, so both the caller-supplied-baseline
    guard below and ``resolve_compliance_result``'s own baseline lookup agree
    on what "this evidence's product" means — a value the extractor
    transcribed and validation accepted, not a string parsed out of the
    batch id, which is an identifier scheme choice with no guarantee of
    carrying the product code at all.
    """
    field = next(
        (
            f
            for f in evidence.fields
            if f.field_name == "product_code"
            and f.status is FieldStatus.ACCEPTED
            and isinstance(f.typed_value, str)
        ),
        None,
    )
    return field.typed_value if field else None


def _require_baseline_matches_product(
    evidence: ValidatedEvidence, baseline: TrendBaseline
) -> None:
    """Guards against scoring a batch against another product's baseline.

    Only enforced when the evidence's own ``product_code`` field is present
    and settled. If it is not (missing, unreadable, or still under review),
    this cannot check anything either way — that gap is already a
    completeness finding from ``check_completeness``, so it is surfaced there
    rather than raised here as a second, less specific error.

    Raises:
        ValueError: If the evidence's settled product code does not match the
            baseline's ``product_code``.
    """
    extracted = _extracted_product_code(evidence)
    if extracted is not None and extracted != baseline.product_code:
        raise ValueError(
            f"Trend baseline {baseline.baseline_id!r} is approved for product "
            f"{baseline.product_code!r} but this evidence's own product_code "
            f"reading is {extracted!r}."
        )


def evaluate_compliance(
    evidence: ValidatedEvidence,
    *,
    spec: DocumentFieldSpec | None = None,
    baseline: TrendBaseline | None = None,
) -> ComplianceResult:
    """Runs all three deterministic checks and assembles the result.

    Args:
        evidence: Validated evidence for one artifact.
        spec: Approved field specification to check against. Defaults to the
            spec in force; an explicit spec is accepted so a case can be
            re-evaluated under the specification it was originally validated
            against.
        baseline: Approved trend baseline. If ``None``, the trend-conformance
            check is skipped entirely and no trend-related finding or audit
            field is produced — a case with no approved baseline for its
            product is not silently treated as passing OOT, it is treated as
            OOT not being evaluated, which is a different and honestly
            weaker claim.

    Returns:
        A ``ComplianceResult``. ``status`` is
        ``SUFFICIENT_FOR_INVESTIGATION`` only when there are zero findings of
        any type; otherwise ``INSUFFICIENT_EVIDENCE``. This is deliberately
        the strict reading: a single OOS or completeness finding means the
        record cannot yet support an investigation team using it as settled
        fact, even though the finding itself might become the investigation's
        starting point once a human is in the loop.

    Raises:
        ValueError: If the evidence was extracted/validated under a
            different specification than the one it is being checked against
            (mirrors the same check in ``evidence_validation``, for the same
            reason: a finding attributed to the wrong spec is unattributable).
    """
    spec = spec or load_document_field_spec()
    if evidence.spec_sha256 != spec.spec_sha256:
        raise ValueError(
            f"Evidence was validated under spec {evidence.spec_version} "
            f"({evidence.spec_sha256[:12]}) but compliance is being checked "
            f"against {spec.spec_version} ({spec.spec_sha256[:12]})."
        )
    if evidence.spec_id != spec.spec_id:
        # Defensive, beyond the hash check above, mirroring the same
        # belt-and-suspenders check in evidence_validation.validate_extracted_
        # evidence: a spec-id mismatch masked by a coincidental hash match is
        # not something SHA-256 alone rules out if a second document type is
        # ever introduced under this pipeline.
        raise ValueError(
            f"Evidence was validated under spec id {evidence.spec_id!r} but "
            f"compliance is being checked against {spec.spec_id!r}."
        )
    if baseline is not None:
        _require_baseline_matches_product(evidence, baseline)

    findings: list[ComplianceFinding] = []
    findings.extend(check_completeness(spec, evidence))
    findings.extend(check_specification_conformance(spec, evidence))
    if baseline is not None:
        findings.extend(check_trend_conformance(spec, evidence, baseline))

    status = (
        ComplianceStatus.SUFFICIENT_FOR_INVESTIGATION
        if not findings
        else ComplianceStatus.INSUFFICIENT_EVIDENCE
    )

    def _count(finding_type: ComplianceFindingType) -> int:
        return sum(1 for finding in findings if finding.finding_type is finding_type)

    audit = ComplianceAuditEvent(
        tenant_id=evidence.tenant_id,
        case_id=evidence.case_id,
        batch_id=evidence.batch_id,
        artifact_filename=evidence.artifact_filename,
        artifact_version=evidence.artifact_version,
        artifact_sha256=evidence.artifact_sha256,
        spec_id=evidence.spec_id,
        spec_version=evidence.spec_version,
        spec_sha256=evidence.spec_sha256,
        trend_baseline_id=baseline.baseline_id if baseline else None,
        trend_baseline_version=baseline.baseline_version if baseline else None,
        trend_baseline_sha256=baseline.baseline_sha256 if baseline else None,
        status=status,
        completeness_finding_count=_count(ComplianceFindingType.COMPLETENESS),
        oos_finding_count=_count(ComplianceFindingType.OUT_OF_SPECIFICATION),
        oot_finding_count=_count(ComplianceFindingType.OUT_OF_TREND),
    )

    return ComplianceResult(
        tenant_id=evidence.tenant_id,
        case_id=evidence.case_id,
        batch_id=evidence.batch_id,
        artifact_filename=evidence.artifact_filename,
        artifact_version=evidence.artifact_version,
        artifact_sha256=evidence.artifact_sha256,
        spec_id=evidence.spec_id,
        spec_version=evidence.spec_version,
        spec_sha256=evidence.spec_sha256,
        trend_baseline_id=baseline.baseline_id if baseline else None,
        trend_baseline_version=baseline.baseline_version if baseline else None,
        trend_baseline_sha256=baseline.baseline_sha256 if baseline else None,
        status=status,
        findings=findings,
        root_cause_summary=_root_cause_summary(findings),
        source_evidence=evidence,
        audit=audit,
    )


def _resolve_trend_baseline_filename(product_code: str) -> str | None:
    """Maps a product code to its trend baseline filename, if one is approved.

    A small, explicit mapping rather than a filename convention derived from
    the product code: which products have an approved baseline is itself a
    change-controlled fact, not something to infer from string matching.
    """
    mapping = {"AXR450": "trend_baseline_axr450_v1.json"}
    return mapping.get(product_code)


def resolve_compliance_result(evidence: ValidatedEvidence) -> ComplianceResult:
    """Runs ``evaluate_compliance`` with the trend baseline resolved for the
    evidence's own product.

    Resolves the trend baseline from the evidence's extracted ``product_code``
    field when present and settled; when no product code is available or no
    baseline is approved for it, the OOT check is skipped rather than guessed.
    This is a plain function — the ``@node``-wrapped
    ``evaluate_evidence_compliance`` below only adapts it to the workflow
    graph — so it can be unit-tested directly without going through ADK's
    node-execution machinery.

    Args:
        evidence: Validated evidence from the extraction stage.

    Returns:
        A ``ComplianceResult`` with completeness and OOS findings always
        evaluated, and OOT findings evaluated when an approved baseline for
        the evidence's product is found.
    """
    product_code = _extracted_product_code(evidence)

    baseline = None
    if product_code is not None:
        filename = _resolve_trend_baseline_filename(product_code)
        if filename is not None:
            baseline = load_trend_baseline(filename)

    return evaluate_compliance(evidence, baseline=baseline)


@node(name="evaluate_evidence_compliance")
def evaluate_evidence_compliance(node_input: ValidatedEvidence) -> ComplianceResult:
    """Workflow node wrapping ``resolve_compliance_result``.

    Args:
        node_input: Validated evidence from the extraction stage.

    Returns:
        See ``resolve_compliance_result``.
    """
    return resolve_compliance_result(node_input)
