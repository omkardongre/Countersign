#!/usr/bin/env python3
"""Provision the durable CFI aggregate table with least privilege.

The department MCP service is the only process that opens this connection at
runtime. The CFI Agent Runtime reaches the table through cfi_open/cfi_get/
cfi_save over MCP and never receives a Cloud SQL credential.
"""

from __future__ import annotations

import os
import subprocess

from google.cloud.sql.connector import Connector, IPTypes

PROJECT_ID = os.environ.get("PROJECT_ID", "countersign-505107")
INSTANCE = os.environ.get(
    "INSTANCE_CONNECTION_NAME", "countersign-505107:us-central1:countersign-sessions"
)
DB_NAME = os.environ.get("CFI_DB_NAME", os.environ.get("DB_NAME", "adk_sessions"))
IAM_USER = os.environ.get(
    "CFI_SQL_IAM_USER",
    os.environ.get("DB_IAM_USER", "countersign-department-mcp@countersign-505107.iam"),
)
SECRET_ID = os.environ.get(
    "CFI_SQL_BOOTSTRAP_SECRET",
    os.environ.get("SESSIONS_SQL_BOOTSTRAP_SECRET", "countersign-sessions-postgres"),
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
    quoted_user = IAM_USER.replace('"', '""')
    ddl = f"""
CREATE TABLE IF NOT EXISTS cfi_investigation (
    cfi_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    source_case_id TEXT NOT NULL,
    outcome TEXT,
    revision BIGINT NOT NULL DEFAULT 0,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cfi_investigation_tenant_case_key
        UNIQUE (tenant_id, source_case_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS cfi_investigation_tenant_case_uidx
    ON cfi_investigation (tenant_id, source_case_id);
CREATE INDEX IF NOT EXISTS cfi_investigation_tenant_batch_idx
    ON cfi_investigation (tenant_id, batch_id);
GRANT CONNECT ON DATABASE {DB_NAME} TO "{quoted_user}";
GRANT USAGE ON SCHEMA public TO "{quoted_user}";
-- The MCP store needs SELECT for get/compare-and-save, INSERT for atomic open,
-- and UPDATE for the optimistic revision write. It has no DELETE or DDL grant.
GRANT SELECT, INSERT, UPDATE ON cfi_investigation TO "{quoted_user}";
"""

    connector = Connector()
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
        for statement in ddl.split(";"):
            body = statement.strip()
            if body:
                cursor.execute(body)
        conn.commit()
    finally:
        conn.close()
        connector.close()

    print(
        f"Ensured {DB_NAME}.cfi_investigation on {INSTANCE}; granted "
        f"SELECT/INSERT/UPDATE only to {IAM_USER}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
