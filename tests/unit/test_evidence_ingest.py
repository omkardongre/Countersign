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

"""Unit tests for deterministic evidence ingest and the pinned document spec."""

import base64
import hashlib

import pytest

from app.domain.evidence_models import MAX_EVIDENCE_BYTES, EvidenceIngestRequest
from app.domain.ingest import (
    EvidenceIntegrityError,
    build_artifact_filename,
    decode_and_verify_payload,
)
from app.domain.master_data import FieldKind, load_document_field_spec
from tests.unit.evidence_fixtures import SYNTHETIC_PAGE_BYTES, make_ingest_request


def test_payload_is_decoded_and_hashed() -> None:
    request = make_ingest_request()

    payload, digest = decode_and_verify_payload(request)

    assert payload == SYNTHETIC_PAGE_BYTES
    assert digest == hashlib.sha256(SYNTHETIC_PAGE_BYTES).hexdigest()


def test_disallowed_mime_type_is_refused() -> None:
    request = make_ingest_request(mime_type="application/zip")

    with pytest.raises(EvidenceIntegrityError, match="not an accepted evidence type"):
        decode_and_verify_payload(request)


def test_invalid_base64_is_refused() -> None:
    request = make_ingest_request(content_base64="not base64 !!!")

    with pytest.raises(EvidenceIntegrityError, match="not valid base64"):
        decode_and_verify_payload(request)


def test_empty_payload_is_refused() -> None:
    request = make_ingest_request(content_base64="")

    with pytest.raises(EvidenceIntegrityError, match="empty"):
        decode_and_verify_payload(request)


def test_oversized_payload_is_refused() -> None:
    oversized = base64.b64encode(b"\x00" * (MAX_EVIDENCE_BYTES + 1)).decode()
    request = make_ingest_request(content_base64=oversized)

    with pytest.raises(EvidenceIntegrityError, match="per-artifact limit"):
        decode_and_verify_payload(request)


@pytest.mark.parametrize(
    "tenant_id",
    ["../escape", "tenant/with/slash", "", "a" * 100],
)
def test_unsafe_tenant_scope_is_refused(tenant_id: str) -> None:
    """Scope ids build artifact filenames, so they must not be able to traverse."""
    request = make_ingest_request(tenant_id=tenant_id)

    with pytest.raises(EvidenceIntegrityError, match="valid scope identifier"):
        decode_and_verify_payload(request)


def test_artifact_filename_is_content_addressed_and_scoped() -> None:
    digest = "a" * 64
    filename = build_artifact_filename(
        tenant_id="aurex", case_id="case-1", sha256=digest, mime_type="image/png"
    )

    assert filename == f"evidence__aurex__case-1__{digest[:16]}.png"
    # Same bytes for the same case must resolve to the same artifact, so an
    # at-least-once resume cannot duplicate a source record.
    assert filename == build_artifact_filename(
        tenant_id="aurex", case_id="case-1", sha256=digest, mime_type="image/png"
    )


def test_ingest_request_requires_at_least_one_page() -> None:
    with pytest.raises(ValueError):
        EvidenceIngestRequest(
            tenant_id="aurex",
            case_id="case-1",
            batch_id="AX-2291",
            source_uri="drive://synthetic",
            mime_type="image/png",
            content_base64=base64.b64encode(SYNTHETIC_PAGE_BYTES).decode(),
            page_count=0,
        )


def test_document_spec_is_pinned_and_hashed() -> None:
    spec = load_document_field_spec()

    assert spec.spec_version == "1.0.0"
    assert len(spec.spec_sha256) == 64
    # Cached, so a case in flight cannot observe the spec changing underneath it.
    assert load_document_field_spec() is spec


def test_critical_fields_carry_the_higher_confidence_bar() -> None:
    spec = load_document_field_spec()
    critical = spec.field("blend_speed_rpm")
    standard = spec.field("product_temperature_c")

    assert critical is not None and critical.critical is True
    assert standard is not None and standard.critical is False
    assert spec.min_confidence_for(critical) == spec.critical_field_min_confidence
    assert spec.min_confidence_for(standard) == spec.standard_field_min_confidence
    assert spec.critical_field_min_confidence > spec.standard_field_min_confidence


def test_spec_declares_units_and_kinds_for_process_parameters() -> None:
    spec = load_document_field_spec()
    blend_speed = spec.field("blend_speed_rpm")

    assert blend_speed is not None
    assert blend_speed.kind is FieldKind.NUMBER
    assert blend_speed.unit == "rpm"
    assert blend_speed.minimum == 8.0
    assert blend_speed.maximum == 14.0


def test_unknown_field_is_not_resolvable() -> None:
    assert load_document_field_spec().field("invented_field") is None
