#!/usr/bin/env python3
"""Provision Cloud SQL LIMS and sponsor case-status tables with least privilege.

The LIMS table receives SELECT-only access; the separate batch-keyed
``case_status`` table receives the read/write access its workflow updates need.
Both are provisioned by the same trusted bootstrap path, never by an agent.
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
SIBLING_BATCH_TABLE_NAME = os.environ.get(
    "SIBLING_BATCH_TABLE_NAME", "batch_material_lots"
)
SESSIONS_DB_NAME = os.environ.get("SESSIONS_DB_NAME", "adk_sessions")
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
-- Cross-batch material-lot table for CFI scope expansion (21 CFR 211.192's
-- extend-to-associated-batches duty). Its own table, not a join against
-- lims_results: material lot is recorded once per batch regardless of
-- which LIMS assay rows exist. SELECT only, same GMP data-integrity
-- reasoning as the LIMS grant above.
CREATE TABLE IF NOT EXISTS {SIBLING_BATCH_TABLE_NAME} (
    tenant_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    material_lot TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, batch_id)
);
CREATE INDEX IF NOT EXISTS {SIBLING_BATCH_TABLE_NAME}_tenant_lot_idx
    ON {SIBLING_BATCH_TABLE_NAME} (tenant_id, material_lot);
GRANT SELECT ON {SIBLING_BATCH_TABLE_NAME} TO "{quoted}";
-- Seed rows matching the existing synthetic catalog's shared lot
-- (aurex/AX-2291, AX-2292, AX-2293 all cite LOT-88), so a CFI scope
-- expansion behaves identically whether COUNTERSIGN_SIBLING_BATCH_BACKEND
-- is on or off.
INSERT INTO {SIBLING_BATCH_TABLE_NAME} (tenant_id, batch_id, material_lot, source_uri)
VALUES
    ('aurex', 'AX-2291', 'LOT-88', 'drive://synthetic/coa/MAT-441-LOT-88.pdf'),
    ('aurex', 'AX-2292', 'LOT-88', 'drive://synthetic/coa/MAT-441-LOT-88.pdf'),
    ('aurex', 'AX-2293', 'LOT-88', 'drive://synthetic/coa/MAT-441-LOT-88.pdf')
ON CONFLICT (tenant_id, batch_id) DO NOTHING;
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
        # CREATE DATABASE cannot run inside a transaction block; autocommit
        # must be on before the very first statement on this connection, not
        # just before the CREATE DATABASE call itself.
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
        if cursor.fetchone() is None:
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

    conn = connector.connect(
        INSTANCE,
        "pg8000",
        user="postgres",
        db=SESSIONS_DB_NAME,
        password=password,
        ip_type=IPTypes.PUBLIC,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS case_status (
                tenant_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                status TEXT NOT NULL,
                sterility_due_at TIMESTAMPTZ,
                qp_confirmed_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (tenant_id, batch_id)
            )
            """
        )
        cursor.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON case_status TO "{quoted}"')
        conn.commit()
    finally:
        conn.close()
        connector.close()
    print(
        f"Created {DB_NAME}.{TABLE_NAME}, seeded aurex/AX-2291, "
        f"granted SELECT-only to {IAM_USER}; created "
        f"{DB_NAME}.{SIBLING_BATCH_TABLE_NAME} seeded with the shared-lot "
        "siblings AX-2291/AX-2292/AX-2293, granted SELECT-only; ensured "
        f"{SESSIONS_DB_NAME}.case_status with status read/write access."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
