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

"""Process-wide ADK session/artifact services shared by every serving surface.

Registered under ``shared://`` so the ADK web routes, the A2A path, and the
reasoning_engine adapter share one instance: a session created on any surface
is visible to the others.
"""

from __future__ import annotations

import functools
import os

from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.cli.service_registry import get_service_registry
from google.adk.cli.utils.service_factory import create_session_service_from_options

from app.app_utils.cloud_sql import (
    create_async_session_engine,
    instance_connection_name,
)

SESSION_SERVICE_URI = "shared://session"
ARTIFACT_SERVICE_URI = "shared://artifact"

_AGENT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


@functools.cache
def get_session_service():
    """Process-wide session service shared across every serving surface."""
    if instance_connection_name():
        from google.adk.sessions.database_session_service import DatabaseSessionService

        return DatabaseSessionService(db_engine=create_async_session_engine())
    if uri := os.environ.get("SESSION_SERVICE_URI"):
        # postgresql+asyncpg://… → DatabaseSessionService (proxy / local).
        return create_session_service_from_options(
            base_dir=_AGENT_DIR, session_service_uri=uri
        )
    if os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
        if os.environ.get("COUNTERSIGN_ALLOW_VERTEX_SESSIONS", "").strip() in (
            "1",
            "true",
            "TRUE",
        ):
            from google.adk.sessions.vertex_ai_session_service import (
                VertexAiSessionService,
            )

            return VertexAiSessionService(
                project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                location=os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
                or os.environ.get("GOOGLE_CLOUD_LOCATION"),
                agent_engine_id=os.environ["GOOGLE_CLOUD_AGENT_ENGINE_ID"],
            )
        raise RuntimeError(
            "Agent Runtime must set INSTANCE_CONNECTION_NAME for "
            "DatabaseSessionService. Vertex sessions hide SQL rows. Set "
            "COUNTERSIGN_ALLOW_VERTEX_SESSIONS=1 only if that is intentional."
        )
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    return InMemorySessionService()


@functools.cache
def get_artifact_service():
    """Process-wide artifact service: GCS when a bucket is set, else in-memory."""
    if bucket := os.environ.get("LOGS_BUCKET_NAME"):
        return GcsArtifactService(bucket_name=bucket)
    return InMemoryArtifactService()


_registry = get_service_registry()
_registry.register_session_service("shared", lambda uri, **kw: get_session_service())
_registry.register_artifact_service("shared", lambda uri, **kw: get_artifact_service())


def get_google_credentials(*, scopes: list[str]):
    """Ambient service-account credentials scoped for a specific Google API.

    Same ADC pattern already used in ``app/mcp/client.py`` for the MCP
    invoker token — ``google.auth.default`` picks up the department MCP
    Cloud Run service's own service account in production, or the
    operator's ``gcloud auth application-default login`` credentials
    locally. Not cached: a scoped credentials object is cheap to build and
    a caller may request different scopes.
    """
    import google.auth

    credentials, _ = google.auth.default(scopes=scopes)
    return credentials
