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

"""Unit tests default to in-process department MCP (explicit opt-in)."""

from __future__ import annotations

import pytest

from app.domain.case_memory_store import (
    get_case_memory_store,
    reset_case_memory_store_cache,
)
from app.domain.sterility_store import get_sterility_store, reset_sterility_store_cache


@pytest.fixture(autouse=True)
def _department_mcp_inprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COUNTERSIGN_MCP_INPROCESS", "1")


@pytest.fixture(autouse=True)
def _fresh_case_memory_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Firestore in unit tests: explicit opt-out, like MCP in-process."""
    monkeypatch.setenv("COUNTERSIGN_CASE_MEMORY_INMEMORY", "1")
    reset_case_memory_store_cache()
    store = get_case_memory_store()
    if hasattr(store, "reset"):
        store.reset()
    yield
    reset_case_memory_store_cache()


@pytest.fixture(autouse=True)
def _fresh_sterility_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COUNTERSIGN_STERILITY_DB", raising=False)
    monkeypatch.delenv("COUNTERSIGN_STERILITY_HOLD_SECONDS", raising=False)
    monkeypatch.delenv("INSTANCE_CONNECTION_NAME", raising=False)
    monkeypatch.delenv("DB_IAM_USER", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", raising=False)
    monkeypatch.delenv("COUNTERSIGN_ALLOW_VERTEX_SESSIONS", raising=False)
    monkeypatch.delenv("COUNTERSIGN_CASE_ENGINE", raising=False)
    reset_sterility_store_cache()
    get_sterility_store().reset()
    yield
    reset_sterility_store_cache()


@pytest.fixture
def sterility_released() -> None:
    """Mark the synthetic AX-2291 incubation complete so later nodes can run."""
    store = get_sterility_store()
    store.start(
        tenant_id="aurex",
        batch_id="AX-2291",
        case_id="pre-released",
        workflow_version="test",
    )
    store.complete("aurex", "AX-2291")
