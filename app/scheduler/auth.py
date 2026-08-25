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

"""OIDC verification for the scheduler tick.

The tick can complete a GMP hold and wake a parked case, so it is an
authenticated control-plane call, not an open webhook. Cloud Scheduler signs
each request with an OIDC token; this module verifies the signature, the
audience, and the calling service account.

Configuration:

* ``COUNTERSIGN_SCHEDULER_AUDIENCE`` — required. Comma-separated set of
  ``aud`` claims Cloud Scheduler is configured to mint — one per tick route
  (sterility-tick, cfi-tick), since each is invoked by its own Scheduler job
  targeting its own URL.
* ``COUNTERSIGN_SCHEDULER_SERVICE_ACCOUNTS`` — required. Comma-separated
  allow-list; the verified ``email`` claim must match one of them.
* ``COUNTERSIGN_SCHEDULER_AUTH_DISABLED`` — local development only. Logs a
  warning on every bypass so it cannot be mistaken for a configured deploy.

Fails closed: missing configuration returns 500 rather than admitting an
unauthenticated caller.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException, Request

AUDIENCE_ENV = "COUNTERSIGN_SCHEDULER_AUDIENCE"
SERVICE_ACCOUNTS_ENV = "COUNTERSIGN_SCHEDULER_SERVICE_ACCOUNTS"
AUTH_DISABLED_ENV = "COUNTERSIGN_SCHEDULER_AUTH_DISABLED"

logger = logging.getLogger(__name__)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _allowed_service_accounts() -> set[str]:
    raw = os.environ.get(SERVICE_ACCOUNTS_ENV, "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _allowed_audiences() -> set[str]:
    # Both /scheduler/sterility-tick and /scheduler/cfi-tick share this one
    # router/dependency. Each is invoked by its own Cloud Scheduler job with
    # its own OIDC audience, so this accepts a comma-separated set rather
    # than forcing every tick route onto one fixed audience string.
    raw = os.environ.get(AUDIENCE_ENV, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _verify_oidc_token(token: str, audience: str) -> dict[str, Any]:
    """Verify signature and audience. Separated so tests can substitute it."""
    import google.auth.transport.requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(
        token, google.auth.transport.requests.Request(), audience
    )


def verify_scheduler_token(request: Request) -> None:
    """FastAPI dependency guarding the scheduler routes."""
    if _flag(AUTH_DISABLED_ENV):
        logger.warning(
            "%s is set: scheduler tick is unauthenticated. Local development only.",
            AUTH_DISABLED_ENV,
        )
        return

    audiences = _allowed_audiences()
    allowed = _allowed_service_accounts()
    if not audiences or not allowed:
        # Misconfiguration must not degrade into an open endpoint.
        raise HTTPException(
            status_code=500,
            detail=(
                f"Scheduler auth is not configured: set {AUDIENCE_ENV} and "
                f"{SERVICE_ACCOUNTS_ENV}, or {AUTH_DISABLED_ENV} for local runs."
            ),
        )

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")

    claims: dict[str, Any] | None = None
    last_error: Exception | None = None
    for audience in audiences:
        try:
            claims = _verify_oidc_token(token, audience)
            break
        except Exception as exc:  # try the next configured audience
            last_error = exc
    if claims is None:
        logger.warning("Scheduler token rejected: %s", last_error)
        raise HTTPException(status_code=401, detail="Invalid token.") from last_error

    email = str(claims.get("email", "")).strip().lower()
    if email not in allowed:
        logger.warning("Scheduler caller %r is not allow-listed.", email)
        raise HTTPException(status_code=403, detail="Caller not allowed.")
