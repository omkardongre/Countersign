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

"""Real environmental monitoring system: cleanroom readings in BigQuery.

A separate department from QC lab, on a separate system, reached by a
separate agent with a separate tool — which is the point. The QC lab agent
holds no BigQuery credential and the EM agent holds no Cloud SQL credential;
neither holds either, because both reach their system through the department
MCP process, and each is bound to exactly one tool.

BigQuery is the right shape for EM specifically: readings are high-volume,
append-only, timestamped telemetry per room and per parameter, which is what
a columnar warehouse is for, whereas assay results are low-volume
transactional records that belong in Postgres. The split is a data-modelling
consequence, not two databases chosen for variety.

Read-only by construction: ``SELECT`` only, so no agent path can alter a
recorded cleanroom reading.
"""

from __future__ import annotations

import functools
import os

from app.domain.department_models import EmReadingRow, EmSnapshot
from app.domain.department_source import (
    BACKEND_BIGQUERY,
    DepartmentRecordMissing,
    em_dataset_name,
    em_table_name,
)

EM_SCHEMA_FIELDS = (
    ("tenant_id", "STRING", "REQUIRED"),
    ("batch_id", "STRING", "REQUIRED"),
    ("location_id", "STRING", "REQUIRED"),
    ("parameter", "STRING", "REQUIRED"),
    ("label", "STRING", "REQUIRED"),
    ("value", "FLOAT64", "REQUIRED"),
    ("unit", "STRING", "REQUIRED"),
    ("alert_limit", "FLOAT64", "REQUIRED"),
    ("action_limit", "FLOAT64", "REQUIRED"),
    ("recorded_at", "TIMESTAMP", "REQUIRED"),
)

_SELECT_TEMPLATE = """
SELECT location_id, parameter, label, value, unit,
       alert_limit, action_limit, recorded_at
FROM `{project}.{dataset}.{table}`
WHERE tenant_id = @tenant_id AND batch_id = @batch_id
ORDER BY recorded_at, location_id, parameter
"""


def em_project_id() -> str:
    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        or os.environ.get("PROJECT_ID", "").strip()
    )
    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT (or PROJECT_ID) must be set to read "
            "environmental monitoring from BigQuery."
        )
    return project


@functools.cache
def _client():
    from google.cloud import bigquery

    return bigquery.Client(project=em_project_id())


def em_table_fqn() -> str:
    return f"{em_project_id()}.{em_dataset_name()}.{em_table_name()}"


class BigQueryEmSource:
    """BigQuery-backed environmental monitoring reads."""

    backend_name = BACKEND_BIGQUERY

    def source_uri(self, *, tenant_id: str, batch_id: str) -> str:
        """Citable identifier naming the real table and the exact predicate."""
        return (
            f"bq://{em_table_fqn()}"
            f"?tenant_id={tenant_id}&batch_id={batch_id}"
        )

    def em(self, *, tenant_id: str, batch_id: str) -> EmSnapshot:
        from google.cloud import bigquery

        # Bound query parameters, not string interpolation: tenant_id is the
        # tenant isolation predicate, so it is exactly the value that must
        # never be able to alter the query's structure. Dataset and table are
        # validated as identifiers upstream, since SQL cannot bind them.
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
                bigquery.ScalarQueryParameter("batch_id", "STRING", batch_id),
            ]
        )
        query = _SELECT_TEMPLATE.format(
            project=em_project_id(),
            dataset=em_dataset_name(),
            table=em_table_name(),
        )
        rows = list(_client().query(query, job_config=job_config).result())
        if not rows:
            raise DepartmentRecordMissing(
                f"No EM readings in {em_table_fqn()} for {tenant_id}/{batch_id}."
            )
        return EmSnapshot(
            system=BACKEND_BIGQUERY,
            source_uri=self.source_uri(tenant_id=tenant_id, batch_id=batch_id),
            rows=[
                EmReadingRow(
                    location_id=row["location_id"],
                    parameter=row["parameter"],
                    label=row["label"],
                    value=float(row["value"]),
                    unit=row["unit"],
                    alert_limit=float(row["alert_limit"]),
                    action_limit=float(row["action_limit"]),
                    recorded_at=row["recorded_at"],
                )
                for row in rows
            ],
        )
