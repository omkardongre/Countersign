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

"""Cross-company caller authentication for the sponsor liaison.

The sponsor's own agent is not on this project's IAM, so the identity
mechanisms the rest of Countersign uses (Agent Identity SPIFFE, IAP OIDC
audience/service-account allow-list — see ``app/scheduler/auth.py``) do not
apply to it: those all assume the caller is provisioned inside
``countersign-505107``. A sponsor's agent is provisioned inside its own
organization instead.

So the sponsor boundary uses the mechanism that *does* work across
organizations without either side depending on the other's IAM: a per-sponsor
bearer API key, issued by this CDMO out of band as part of onboarding that
sponsor's Quality Agreement, and never accepted as a model-supplied claim.
Whoever presents ``sponsor-aurex-qa``'s key *is* sponsor-aurex-qa for this
call — the same "the model supplies no scope arguments" discipline every
department tool closure already applies (``app/domain/branches.py``), just
enforced at the transport boundary instead of inside a tool closure, because
here the caller is a different company's process, not a tool this service
itself invokes.

Configuration:

* ``COUNTERSIGN_SPONSOR_API_KEYS`` — required in production.
  ``key:sponsor_id,key:sponsor_id`` pairs. A key must be unique to exactly
  one ``sponsor_id``.
* ``COUNTERSIGN_SPONSOR_LIAISON_AUTH_DISABLED`` — local development only.
  Logs a warning on every bypass, matching ``app/scheduler/auth.py``'s
  ``AUTH_DISABLED_ENV`` so a bypass can never be mistaken for a configured
  deploy.

Fails closed: missing configuration is a startup error, not an admitted
unauthenticated caller.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request

API_KEYS_ENV = "COUNTERSIGN_SPONSOR_API_KEYS"
AUTH_DISABLED_ENV = "COUNTERSIGN_SPONSOR_LIAISON_AUTH_DISABLED"

logger = logging.getLogger(__name__)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def parse_sponsor_api_keys(raw: str) -> dict[str, str]:
    """``key:sponsor_id,key:sponsor_id`` -> dict. Strict, fails loudly.

    A malformed entry or a key claimed by two sponsors must be a startup
    error, not a warning that leaves a half-parsed map silently missing an
    entry — the same reasoning as
    ``app/domain/sponsor_tools.py::_parse_sponsor_tenant_map``.
    """
    mapping: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise RuntimeError(f"{API_KEYS_ENV} entry {entry!r} is not key:sponsor_id.")
        key, _, sponsor_id = entry.partition(":")
        key, sponsor_id = key.strip(), sponsor_id.strip()
        if not key or not sponsor_id:
            raise RuntimeError(
                f"{API_KEYS_ENV} entry {entry!r} has an empty key or sponsor_id."
            )
        if key in mapping and mapping[key] != sponsor_id:
            raise RuntimeError(
                f"{API_KEYS_ENV} lists key {key[:8]}... for both "
                f"{mapping[key]!r} and {sponsor_id!r}. A key must map to "
                "exactly one sponsor."
            )
        mapping[key] = sponsor_id
    return mapping


def sponsor_api_keys() -> dict[str, str]:
    raw = os.environ.get(API_KEYS_ENV, "").strip()
    if not raw:
        return {}
    return parse_sponsor_api_keys(raw)


def authenticate_sponsor_caller(request: Request) -> str:
    """Return the authenticated ``sponsor_id``, or raise 401/403/500.

    Used as a request-scoped dependency, not module state: each call
    re-reads the header, so there is no window where one request's
    identity could leak into another's.
    """
    if _flag(AUTH_DISABLED_ENV):
        logger.warning(
            "%s is set: sponsor liaison accepts an unauthenticated "
            "sponsor_id from a header. Local development only.",
            AUTH_DISABLED_ENV,
        )
        sponsor_id = request.headers.get("x-countersign-dev-sponsor-id", "")
        if not sponsor_id:
            raise HTTPException(
                status_code=400,
                detail="Set X-Countersign-Dev-Sponsor-Id when auth is disabled.",
            )
        return sponsor_id

    keys = sponsor_api_keys()
    if not keys:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Sponsor liaison auth is not configured: set {API_KEYS_ENV}, "
                f"or {AUTH_DISABLED_ENV} for local runs."
            ),
        )

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing bearer API key.")

    sponsor_id = keys.get(token)
    if sponsor_id is None:
        logger.warning("Sponsor liaison rejected an unrecognized API key.")
        raise HTTPException(status_code=401, detail="Invalid API key.")
    return sponsor_id
