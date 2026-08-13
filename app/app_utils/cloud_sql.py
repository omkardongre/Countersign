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

"""Cloud SQL Connector helpers. IAM auth; no passwords in env or git."""

from __future__ import annotations

import asyncio
import functools
import os
from typing import Any
from weakref import WeakKeyDictionary

INSTANCE_CONNECTION_NAME_ENV = "INSTANCE_CONNECTION_NAME"
DB_NAME_ENV = "DB_NAME"
DB_IAM_USER_ENV = "DB_IAM_USER"
SQL_IP_TYPE_ENV = "COUNTERSIGN_SQL_IP_TYPE"
DEFAULT_DB_NAME = "adk_sessions"

_async_connectors: WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = (
    WeakKeyDictionary()
)


def instance_connection_name() -> str | None:
    value = os.environ.get(INSTANCE_CONNECTION_NAME_ENV, "").strip()
    return value or None


def db_name() -> str:
    return os.environ.get(DB_NAME_ENV, DEFAULT_DB_NAME)


def iam_db_user() -> str:
    value = os.environ.get(DB_IAM_USER_ENV, "").strip()
    if not value:
        raise RuntimeError(
            f"{DB_IAM_USER_ENV} is required when {INSTANCE_CONNECTION_NAME_ENV} is set."
        )
    return value


def _ip_type():
    from google.cloud.sql.connector import IPTypes

    raw = os.environ.get(SQL_IP_TYPE_ENV, "PUBLIC").strip().upper()
    if raw == "PRIVATE":
        return IPTypes.PRIVATE
    if raw == "PUBLIC":
        return IPTypes.PUBLIC
    raise RuntimeError(f"{SQL_IP_TYPE_ENV} must be PUBLIC or PRIVATE, not {raw!r}.")


@functools.cache
def _sync_connector():
    from google.cloud.sql.connector import Connector

    return Connector()


def _async_connector():
    """One Connector per event loop (Cloud SQL Connector loop rule)."""
    from google.cloud.sql.connector import Connector

    loop = asyncio.get_running_loop()
    connector = _async_connectors.get(loop)
    if connector is None:
        connector = Connector(loop=loop)
        _async_connectors[loop] = connector
    return connector


def connect_pg8000(*, user: str | None = None, database: str | None = None):
    """Sync connection for the sterility store. Caller must close it."""
    name = instance_connection_name()
    if not name:
        raise RuntimeError(f"{INSTANCE_CONNECTION_NAME_ENV} is not set.")
    return _sync_connector().connect(
        name,
        "pg8000",
        user=user or iam_db_user(),
        db=database or db_name(),
        enable_iam_auth=True,
        ip_type=_ip_type(),
    )


async def connect_asyncpg(*, user: str | None = None, database: str | None = None):
    name = instance_connection_name()
    if not name:
        raise RuntimeError(f"{INSTANCE_CONNECTION_NAME_ENV} is not set.")
    return await _async_connector().connect_async(
        name,
        "asyncpg",
        user=user or iam_db_user(),
        db=database or db_name(),
        enable_iam_auth=True,
        ip_type=_ip_type(),
    )


def create_async_session_engine():
    """SQLAlchemy async engine for ADK DatabaseSessionService."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    async def _creator() -> Any:
        return await connect_asyncpg()

    return create_async_engine(
        "postgresql+asyncpg://",
        async_creator=_creator,
        poolclass=NullPool,
    )
