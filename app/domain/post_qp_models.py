# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Typed results for isolated post-QP operational requests."""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CorrectiveMaintenanceRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=256)
    batch_id: str = Field(min_length=1, max_length=256)
    summary: str = Field(min_length=1, max_length=1024)
    idempotency_key: str = Field(min_length=16, max_length=128)
    lifecycle_state: str
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Maintenance request cannot claim decision authority.")
        return value


class CorrectiveMaintenanceResult(BaseModel):
    work_order_id: str
    work_order_url: str
    was_existing: bool
    idempotency_key: str
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Maintenance result cannot claim decision authority.")
        return value


class SponsorNotificationRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=256)
    batch_id: str = Field(min_length=1, max_length=256)
    qp_outcome: str = Field(min_length=1, max_length=64)
    qp_recorded_at: datetime.datetime
    idempotency_key: str = Field(min_length=16, max_length=128)
    lifecycle_state: str
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Sponsor notification cannot claim decision authority.")
        return value


class SponsorNotificationResult(BaseModel):
    notification_id: str
    was_existing: bool
    idempotency_key: str
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Sponsor notification result cannot claim decision authority.")
        return value


class PostQpActionError(BaseModel):
    action: Literal["maintenance", "sponsor_notification"]
    error_type: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1024)


class PostQpActionResult(BaseModel):
    maintenance: CorrectiveMaintenanceResult | None = None
    sponsor_notification: SponsorNotificationResult | None = None
    maintenance_error: PostQpActionError | None = None
    sponsor_notification_error: PostQpActionError | None = None
    requested_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    decision_authority: str = "none"

    @field_validator("decision_authority")
    @classmethod
    def _lock_authority(cls, value: str) -> str:
        if value != "none":
            raise ValueError("Post-QP result cannot claim decision authority.")
        return value
