# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Fail-closed tenant authentication for the Sponsor QA receiver."""

from __future__ import annotations

import hmac
import json
import os

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

AUTH_SECRET_ENV = "COUNTERSIGN_SPONSOR_QA_CALLERS_JSON"


class Caller(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    bearer_token: str = Field(min_length=16, max_length=4096)


def authenticate(request: Request) -> str:
    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        raise HTTPException(status_code=401, detail="Bearer authentication is required.")
    try:
        configured = [Caller.model_validate(item) for item in json.loads(os.environ.get(AUTH_SECRET_ENV, ""))]
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise HTTPException(status_code=503, detail="Sponsor QA authentication is not configured.") from exc
    matches = [caller.tenant_id for caller in configured if hmac.compare_digest(caller.bearer_token, supplied)]
    if len(matches) != 1:
        raise HTTPException(status_code=403, detail="Caller is not authorized.")
    return matches[0]
