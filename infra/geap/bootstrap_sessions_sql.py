#!/usr/bin/env python3
"""Grant the IAM db user and create the sterility table. Password from Secret Manager."""

from __future__ import annotations

import os
import subprocess
import sys

from google.cloud.sql.connector import Connector, IPTypes

PROJECT_ID = os.environ.get("PROJECT_ID", "countersign-505107")
INSTANCE = os.environ.get(
    "INSTANCE_CONNECTION_NAME", "countersign-505107:us-central1:countersign-sessions"
)
DB_NAME = os.environ.get("DB_NAME", "adk_sessions")
IAM_USER = os.environ.get("SESSIONS_SQL_IAM_USER", "admin@countersign.site")
SECRET_ID = os.environ.get("SESSIONS_SQL_BOOTSTRAP_SECRET", "countersign-sessions-postgres")


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
GRANT USAGE, CREATE ON SCHEMA public TO "{quoted}";
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO "{quoted}";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO "{quoted}";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL PRIVILEGES ON TABLES TO "{quoted}";
CREATE TABLE IF NOT EXISTS sterility_incubation (
    tenant_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    hold_seconds INTEGER NOT NULL,
    compressed_for_demo BOOLEAN NOT NULL,
    workflow_version TEXT NOT NULL,
    PRIMARY KEY (tenant_id, batch_id)
);
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
        for stmt in ddl.split(";"):
            body = stmt.strip()
            if body:
                cursor.execute(body)
        conn.commit()
    finally:
        conn.close()
        connector.close()
    print(f"Granted {IAM_USER} on {DB_NAME} and ensured sterility_incubation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
