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

"""Pinned synthetic department catalog (LIMS / EM / CoA connector shapes)."""

from __future__ import annotations

import functools
import hashlib
import json
import pathlib

from app.domain.department_models import DepartmentSnapshot

_CATALOG_PATH = (
    pathlib.Path(__file__).parent / "master_data" / "department_catalog_v1.json"
)


@functools.cache
def load_department_catalog() -> tuple[tuple[DepartmentSnapshot, ...], str]:
    """Loads the catalog and the SHA-256 of the exact bytes on disk."""
    raw = _CATALOG_PATH.read_bytes()
    payload = json.loads(raw)
    snapshots = tuple(
        DepartmentSnapshot.model_validate(item) for item in payload["snapshots"]
    )
    return snapshots, hashlib.sha256(raw).hexdigest()


def lookup_department_snapshot(
    *, tenant_id: str, batch_id: str
) -> DepartmentSnapshot | None:
    """Returns the snapshot for one tenant/batch, or None if not in catalog."""
    snapshots, _digest = load_department_catalog()
    for snapshot in snapshots:
        if snapshot.tenant_id == tenant_id and snapshot.batch_id == batch_id:
            return snapshot
    return None
