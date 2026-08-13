#!/usr/bin/env python3
"""Live check: IAM user can write sterility rows and ADK session tables exist."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

os.environ.setdefault(
    "INSTANCE_CONNECTION_NAME",
    "countersign-505107:us-central1:countersign-sessions",
)
os.environ.setdefault("DB_NAME", "adk_sessions")
os.environ.setdefault("DB_IAM_USER", "admin@countersign.site")

from app.app_utils.cloud_sql import connect_pg8000  # noqa: E402
from app.app_utils.services import get_session_service  # noqa: E402
from app.domain.sterility_store import (  # noqa: E402
    get_sterility_store,
    reset_sterility_store_cache,
)


async def _sessions() -> None:
    service = get_session_service()
    session = await service.create_session(
        app_name="countersign-sql-probe",
        user_id="sql-probe",
        session_id=f"probe-{uuid.uuid4()}",
        state={"workflow_version": "deviation_case.1"},
    )
    loaded = await service.get_session(
        app_name="countersign-sql-probe",
        user_id="sql-probe",
        session_id=session.id,
    )
    assert loaded is not None
    assert loaded.state.get("workflow_version") == "deviation_case.1"
    await service.delete_session(
        app_name="countersign-sql-probe",
        user_id="sql-probe",
        session_id=session.id,
    )
    print(f"ADK session round-trip ok ({session.id})")


def main() -> int:
    reset_sterility_store_cache()
    get_session_service.cache_clear()
    store = get_sterility_store()
    batch = f"PROBE-{uuid.uuid4().hex[:8]}"
    store.start(
        tenant_id="aurex",
        batch_id=batch,
        case_id="sql-probe",
        workflow_version="deviation_case.1",
    )
    done = store.complete("aurex", batch)
    assert done.status.value == "complete"
    conn = connect_pg8000()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM sterility_incubation WHERE tenant_id = %s AND batch_id = %s",
            ("aurex", batch),
        )
        row = cursor.fetchone()
        assert row is not None and row[0] == "complete"
        cursor.execute(
            "DELETE FROM sterility_incubation WHERE tenant_id = %s AND batch_id = %s",
            ("aurex", batch),
        )
        conn.commit()
    finally:
        conn.close()
    print(f"sterility_incubation write ok ({batch})")
    asyncio.run(_sessions())
    conn = connect_pg8000()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
            """
        )
        tables = [r[0] for r in cursor.fetchall()]
    finally:
        conn.close()
    print("tables:", ", ".join(tables))
    if "sterility_incubation" not in tables:
        raise SystemExit("sterility_incubation missing")
    if "sessions" not in tables:
        raise SystemExit("ADK sessions table missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
