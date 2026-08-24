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

"""Versioned, change-controlled document-control ledger for T1-5 guidance RAG.

Same pin-and-hash discipline as ``app/domain/master_data.py``: the ledger is
a change-controlled JSON file, not runtime-mutable state, and every load
records the SHA-256 of the exact bytes it was produced under so an auditor
can prove which approved ledger version governed a citation decision.

Vertex AI RAG Engine's own retrieval output carries no version, approval, or
effective-date field (see ``app/domain/guidance_models.py`` module docstring),
so this ledger is the only source of truth for whether a retrieved chunk's
document is actually approved, in force, and in scope for the querying
tenant. A chunk whose ``source_uri`` has no ledger entry is unverifiable and
is dropped by ``app/domain/guidance_source.py``, never trusted on the
strength of the retrieval call alone.
"""

from __future__ import annotations

import functools
import hashlib
import json
import pathlib

from pydantic import BaseModel, ConfigDict

from app.domain.guidance_models import ControlledGuidanceDocument

_MASTER_DATA_DIR = pathlib.Path(__file__).parent / "master_data"

GUIDANCE_MANIFEST_FILENAME = "guidance_manifest_v1.json"


class GuidanceManifest(BaseModel):
    """The whole approved ledger, plus its content hash."""

    model_config = ConfigDict(frozen=True)

    manifest_id: str
    manifest_version: str
    manifest_sha256: str
    effective_date: str
    approved_by: str
    change_control_ref: str
    description: str
    documents: tuple[ControlledGuidanceDocument, ...]

    def by_source_uri(self, source_uri: str) -> ControlledGuidanceDocument | None:
        for document in self.documents:
            if document.source_uri == source_uri:
                return document
        return None

    def by_doc_id(self, doc_id: str) -> ControlledGuidanceDocument | None:
        for document in self.documents:
            if document.doc_id == doc_id:
                return document
        return None


@functools.cache
def load_guidance_manifest(
    filename: str = GUIDANCE_MANIFEST_FILENAME,
) -> GuidanceManifest:
    """Loads and pins the guidance ledger. Cached: this is master data, not
    a per-request read, matching ``load_bmr_page_spec``'s own caching.
    """
    path = _MASTER_DATA_DIR / filename
    raw_bytes = path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    payload = json.loads(raw_bytes)
    documents = tuple(
        ControlledGuidanceDocument.model_validate(entry)
        for entry in payload["documents"]
    )
    return GuidanceManifest(
        manifest_id=payload["manifest_id"],
        manifest_version=payload["manifest_version"],
        manifest_sha256=digest,
        effective_date=payload["effective_date"],
        approved_by=payload["approved_by"],
        change_control_ref=payload["change_control_ref"],
        description=payload["description"],
        documents=documents,
    )
