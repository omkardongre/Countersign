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

"""Deterministic evidence ingest: bytes in, hashed artifact reference out.

Nothing in this module calls a model. It is the boundary that makes the rest of
the extraction stage auditable:

* **MIME allow-list before decode.** A narrow allow-list, checked first, so an
  unexpected payload is refused rather than parsed. (The ADK
  high-volume-document-analyzer sample gets this right and we keep it. The same
  sample disables TLS certificate verification in its fetch path; that is
  rejected outright — a GxP system that trusts unverified transport has no
  provenance claim to make.)
* **Hash at the boundary.** SHA-256 of the exact bytes persisted, computed once
  here, re-verified on every later load. This is what turns "the agent read a
  batch record" into "the agent read *these* bytes", which is the difference
  between a claim and evidence.
* **Tenant on the artifact, not around it.** ``tenant_id`` is written into the
  artifact's own metadata and into its filename, so a later load can be checked
  against the case's tenant instead of trusting the caller's word. Annex 16
  §1.4.3 sponsor separation is only real if it survives a wrong argument.
* **Approved spec pinned at ingest.** The spec version and its content hash are
  captured now, so an extraction run cannot be attributed to a specification
  that was not in force when the evidence entered the case.

Large binaries are persisted through the ADK artifact service rather than
carried in session state, per ADK's own artifact guidance; in the deployed
runtime that is versioned GCS (``GcsArtifactService``, selected in
``app.app_utils.services`` when ``LOGS_BUCKET_NAME`` is set), with the
in-memory service used only for local runs and tests.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re

from google.adk.agents.context import Context
from google.adk.workflow import node
from google.genai import types

from app.domain.evidence_models import (
    ALLOWED_EVIDENCE_MIME_TYPES,
    MAX_EVIDENCE_BYTES,
    EvidenceIngestRequest,
    IngestedArtifact,
)
from app.domain.master_data import load_document_field_spec

_MIME_EXTENSIONS: dict[str, str] = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/tiff": "tif",
    "image/webp": "webp",
}

# Scope identifiers are used to build artifact filenames, so they are held to a
# conservative character set. This is not cosmetic: an unconstrained tenant or
# case id would let a caller reach outside its own artifact namespace.
_SAFE_SCOPE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class EvidenceIntegrityError(ValueError):
    """An evidence payload failed a non-negotiable integrity control.

    Raised rather than returned. A returned error is a value a downstream node
    or model can route around; a raised one terminates the node, which is the
    behaviour a compliance boundary requires.
    """


def _require_safe_scope(name: str, value: str) -> None:
    if not _SAFE_SCOPE_ID.match(value):
        raise EvidenceIntegrityError(
            f"{name} {value!r} is not a valid scope identifier."
        )


def build_artifact_filename(
    *, tenant_id: str, case_id: str, sha256: str, mime_type: str
) -> str:
    """Builds the tenant- and case-scoped artifact filename.

    The content hash is part of the name, so re-ingesting identical bytes for
    the same case resolves to the same artifact instead of creating a second
    copy of the same record. ADK's artifact service guarantees at-least-once
    delivery on resume, so an ingest step that is not content-addressed would
    silently duplicate source records after a replay.
    """
    extension = _MIME_EXTENSIONS[mime_type]
    return f"evidence__{tenant_id}__{case_id}__{sha256[:16]}.{extension}"


def decode_and_verify_payload(request: EvidenceIngestRequest) -> tuple[bytes, str]:
    """Validates and decodes an ingest payload.

    Returns:
        A ``(payload_bytes, sha256_hex)`` pair.

    Raises:
        EvidenceIntegrityError: On any allow-list, size, encoding or scope
            violation. Checked in this order deliberately — cheap, structural
            refusals happen before the payload is decoded into memory.
    """
    _require_safe_scope("tenant_id", request.tenant_id)
    _require_safe_scope("case_id", request.case_id)

    if request.mime_type not in ALLOWED_EVIDENCE_MIME_TYPES:
        raise EvidenceIntegrityError(
            f"MIME type {request.mime_type!r} is not an accepted evidence type. "
            f"Accepted: {sorted(ALLOWED_EVIDENCE_MIME_TYPES)}."
        )

    try:
        payload = base64.b64decode(request.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EvidenceIntegrityError(
            "Evidence payload is not valid base64."
        ) from exc

    if not payload:
        raise EvidenceIntegrityError("Evidence payload is empty.")
    if len(payload) > MAX_EVIDENCE_BYTES:
        raise EvidenceIntegrityError(
            f"Evidence payload is {len(payload)} bytes, above the "
            f"{MAX_EVIDENCE_BYTES}-byte per-artifact limit."
        )

    return payload, hashlib.sha256(payload).hexdigest()


@node(name="ingest_evidence_artifact")
async def ingest_evidence_artifact(
    ctx: Context, node_input: EvidenceIngestRequest
) -> IngestedArtifact:
    """Brings one page of source evidence under case control.

    Args:
        ctx: Workflow context, used only for artifact persistence.
        node_input: The retrieved payload plus its tenant/case/batch scope.

    Returns:
        A hashed, versioned ``IngestedArtifact`` reference. The bytes are not
        returned — only this reference travels to the extractor.

    Raises:
        EvidenceIntegrityError: If the payload violates an integrity control,
            or if the caller pinned a spec version that is not the approved one.
    """
    payload, digest = decode_and_verify_payload(node_input)

    spec = load_document_field_spec()
    if (
        node_input.spec_version_expected is not None
        and node_input.spec_version_expected != spec.spec_version
    ):
        raise EvidenceIntegrityError(
            f"Caller pinned document spec version "
            f"{node_input.spec_version_expected!r} but the approved spec in "
            f"force is {spec.spec_version!r}."
        )

    filename = build_artifact_filename(
        tenant_id=node_input.tenant_id,
        case_id=node_input.case_id,
        sha256=digest,
        mime_type=node_input.mime_type,
    )

    version = await ctx.save_artifact(
        filename=filename,
        artifact=types.Part(
            inline_data=types.Blob(
                data=payload, mime_type=node_input.mime_type
            )
        ),
        custom_metadata={
            # Re-checked on load. Provenance that is not re-verified is a
            # comment, not a control.
            "tenant_id": node_input.tenant_id,
            "case_id": node_input.case_id,
            "batch_id": node_input.batch_id,
            "sha256": digest,
            "source_uri": node_input.source_uri,
            "page_count": node_input.page_count,
            "spec_id": spec.spec_id,
            "spec_version": spec.spec_version,
            "spec_sha256": spec.spec_sha256,
            "decision_authority": "none",
        },
    )

    return IngestedArtifact(
        tenant_id=node_input.tenant_id,
        case_id=node_input.case_id,
        batch_id=node_input.batch_id,
        artifact_filename=filename,
        artifact_version=version,
        artifact_sha256=digest,
        mime_type=node_input.mime_type,
        byte_size=len(payload),
        page_count=node_input.page_count,
        source_uri=node_input.source_uri,
        spec_id=spec.spec_id,
        spec_version=spec.spec_version,
        spec_sha256=spec.spec_sha256,
    )
