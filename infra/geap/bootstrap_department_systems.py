#!/usr/bin/env python3
"""Grant read-only IAM access and create the LIMS table. Password from Secret Manager.

Mirrors bootstrap_sessions_sql.py's pattern (same instance, same IAM/password
split) but for the department-systems database rather than adk_sessions, and
grants SELECT only, not ALL PRIVILEGES: the QC lab agent's tool boundary is
also meant to be the database's own privilege boundary, not just an
application-level convention. Also seeds one row matching the existing
synthetic catalog (tenant aurex, batch AX-2291) so a demo case behaves
identically whether COUNTERSIGN_LIMS_BACKEND is on or off.
"""

from __future__ import annotations

import os
import subprocess
import sys

from google.cloud.sql.connector import Connector, IPTypes

PROJECT_ID = os.environ.get("PROJECT_ID", "countersign-505107")
INSTANCE = os.environ.get(
    "INSTANCE_CONNECTION_NAME", "countersign-505107:us-central1:countersign-sessions"
)
DB_NAME = os.environ.get("LIMS_DB_NAME", "countersign_lims")
TABLE_NAME = os.environ.get("LIMS_TABLE_NAME", "lims_results")
# The department MCP service's own IAM database user (its Cloud Run service
# account), not the human admin account — the MCP process is what actually
# opens this connection at runtime (department_source_postgres.py), so it is
# the principal that needs the grant.
IAM_USER = os.environ.get(
    "SQL_DB_IAM_USER", "countersign-department-mcp@countersign-505107.iam"
)
SECRET_ID = os.environ.get(
    "SESSIONS_SQL_BOOTSTRAP_SECRET", "countersign-sessions-postgres"
)


def _secret() -> str:
    return subprocess.check_output(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={SECRET_ID}",
            f"--project={PROJECT_ID}",
        ],
        text=True,
    ).strip()


def main() -> int:
    password = _secret()
    quoted = IAM_USER.replace('"', '""')
    ddl = f"""
GRANT CONNECT ON DATABASE {DB_NAME} TO "{quoted}";
GRANT USAGE ON SCHEMA public TO "{quoted}";
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    tenant_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    test_code TEXT NOT NULL,
    label TEXT NOT NULL,
    result_value DOUBLE PRECISION NOT NULL,
    unit TEXT NOT NULL,
    spec_min DOUBLE PRECISION NOT NULL,
    spec_max DOUBLE PRECISION NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, batch_id, sample_id, test_code)
);
CREATE INDEX IF NOT EXISTS {TABLE_NAME}_tenant_batch_idx
    ON {TABLE_NAME} (tenant_id, batch_id);
-- SELECT only: the QC lab agent's tool boundary is also the database's own
-- privilege boundary. No agent path can write, update, or delete a
-- laboratory result (GMP data integrity / ALCOA+).
GRANT SELECT ON {TABLE_NAME} TO "{quoted}";
INSERT INTO {TABLE_NAME} (
    tenant_id, batch_id, sample_id, test_code, label,
    result_value, unit, spec_min, spec_max
) VALUES (
    'aurex', 'AX-2291', 'QC-AX-2291-01', 'ASSAY', 'Assay',
    98.4, '%LC', 95.0, 105.0
) ON CONFLICT (tenant_id, batch_id, sample_id, test_code) DO NOTHING;
"""
    connector = Connector()
    conn = connector.connect(
        INSTANCE,
        "pg8000",
        user="postgres",
        db="postgres",
        password=password,
        ip_type=IPTypes.PUBLIC,
    )
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
        if cursor.fetchone() is None:
            conn.autocommit = True
            cursor.execute(f'CREATE DATABASE "{DB_NAME}"')
    finally:
        conn.close()

    conn = connector.connect(
        INSTANCE,
        "pg8000",
        user="postgres",
        db=DB_NAME,
        password=password,
        ip_type=IPTypes.PUBLIC,
    )
    try:
        cursor = conn.cursor()
        for stmt in ddl.split(";"):
            body = stmt.strip()
            if body:
                cursor.execute(body)
        conn.commit()
    finally:
        conn.close()
        connector.close()
    print(
        f"Created {DB_NAME}.{TABLE_NAME}, seeded aurex/AX-2291, "
        f"granted SELECT-only to {IAM_USER}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
