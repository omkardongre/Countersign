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

"""Typed, public-only contracts for T1-7 regulatory screening.

The screening tools deliberately have no tenant, case, batch, internal supplier,
lot, or deviation-text fields. Their inputs are public attributes that may be
sent to an official regulator endpoint. Results are advisory context, not GMP
compliance findings and never establish supplier clearance.
"""

from __future__ import annotations

import datetime
import re
import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RegulatoryScreeningSource(StrEnum):
    EUDRAGMDP = "eudragmdp"
    OPENFDA_ENFORCEMENT = "openfda_enforcement"


class RegulatoryScreeningState(StrEnum):
    HIT = "hit"
    NO_HIT = "no_hit"
    SCREENING_UNAVAILABLE = "screening_unavailable"


class ScreeningCacheStatus(StrEnum):
    LIVE = "live"
    CACHE_HIT = "cache_hit"


class ScreeningUnavailableReason(StrEnum):
    MISSING_PUBLIC_IDENTITY = "missing_public_identity"
    INVALID_PUBLIC_QUERY = "invalid_public_query"
    MCP_UNAVAILABLE = "mcp_unavailable"
    UPSTREAM_TRANSPORT_ERROR = "upstream_transport_error"
    UPSTREAM_HTTP_ERROR = "upstream_http_error"
    UPSTREAM_RATE_LIMITED = "upstream_rate_limited"
    UPSTREAM_RESPONSE_TOO_LARGE = "upstream_response_too_large"
    UPSTREAM_SCHEMA_CHANGED = "upstream_schema_changed"
    AGENT_PROTOCOL_ERROR = "agent_protocol_error"


_PUBLIC_TEXT_MAX_LENGTH = 256
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PublicScreeningQuery(BaseModel):
    """Only public attributes permitted to cross a regulator boundary."""

    model_config = ConfigDict(extra="forbid")

    manufacturer_name: str | None = Field(default=None, max_length=_PUBLIC_TEXT_MAX_LENGTH)
    country: str | None = Field(default=None, max_length=_PUBLIC_TEXT_MAX_LENGTH)
    city: str | None = Field(default=None, max_length=_PUBLIC_TEXT_MAX_LENGTH)
    oms_location_id: str | None = Field(
        default=None, max_length=_PUBLIC_TEXT_MAX_LENGTH
    )
    product_name: str | None = Field(default=None, max_length=_PUBLIC_TEXT_MAX_LENGTH)
    active_ingredient: str | None = Field(
        default=None, max_length=_PUBLIC_TEXT_MAX_LENGTH
    )
    defect_signature: str | None = Field(
        default=None, max_length=_PUBLIC_TEXT_MAX_LENGTH
    )

    @field_validator(
        "manufacturer_name",
        "country",
        "city",
        "oms_location_id",
        "product_name",
        "active_ingredient",
        "defect_signature",
        mode="before",
    )
    @classmethod
    def _validate_public_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("public screening fields must be strings")
        normalized = value.strip()
        if not normalized:
            return None
        if _CONTROL_CHARS.search(normalized):
            raise ValueError("public screening fields may not contain control characters")
        return normalized


class EudraGmdpQuery(PublicScreeningQuery):
    """Public site identity used by the EudraGMDP non-compliance search."""

    @model_validator(mode="after")
    def _require_site_identity(self) -> EudraGmdpQuery:
        if not self.manufacturer_name or not self.country:
            raise ValueError("EudraGMDP screening requires manufacturer_name and country")
        return self


class OpenFdaEnforcementQuery(PublicScreeningQuery):
    """Public terms used by the openFDA drug enforcement search."""

    @model_validator(mode="after")
    def _require_search_term(self) -> OpenFdaEnforcementQuery:
        if not any(
            (
                self.manufacturer_name,
                self.product_name,
                self.active_ingredient,
                self.defect_signature,
            )
        ):
            raise ValueError(
                "openFDA screening requires a public manufacturer, product, "
                "active-ingredient, or defect signature"
            )
        return self


class RegulatoryScreeningRecord(BaseModel):
    """A normalized public record; raw upstream response text is not retained."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1, max_length=256)
    source_url: str
    organization_name: str | None = None
    site_name: str | None = None
    site_address: str | None = None
    country: str | None = None
    city: str | None = None
    oms_location_id: str | None = None
    product_name: str | None = None
    defect_signature: str | None = None
    source_updated_at: datetime.datetime | None = None

    @field_validator("record_id")
    @classmethod
    def _record_id_is_safe_text(cls, value: str) -> str:
        value = value.strip()
        if not value or _CONTROL_CHARS.search(value):
            raise ValueError("regulatory record id is not valid text")
        return value


class ScreeningProvenance(BaseModel):
    """Evidence about how one public result was obtained."""

    model_config = ConfigDict(extra="forbid")

    source: RegulatoryScreeningSource
    source_url: str
    request_url: str
    request_method: Literal["GET", "POST"]
    query: PublicScreeningQuery
    request_parameters: dict[str, str] = Field(default_factory=dict)
    http_status: int | None = None
    retrieved_at: datetime.datetime
    source_updated_at: datetime.datetime | None = None
    cache_status: ScreeningCacheStatus = ScreeningCacheStatus.LIVE
    cache_expires_at: datetime.datetime | None = None
    response_sha256: str | None = None
    parser_version: str
    schema_version: str
    response_truncated: bool = False


class RegulatoryScreeningResult(BaseModel):
    """One source's advisory result, including explicit unavailable state."""

    model_config = ConfigDict(extra="forbid")

    source: RegulatoryScreeningSource
    state: RegulatoryScreeningState
    query: PublicScreeningQuery
    records: list[RegulatoryScreeningRecord] = Field(default_factory=list)
    provenance: ScreeningProvenance
    unavailable_reason: ScreeningUnavailableReason | None = None
    advisory_only: Literal[True] = True

    @model_validator(mode="after")
    def _state_contract(self) -> RegulatoryScreeningResult:
        if self.state is RegulatoryScreeningState.HIT and not self.records:
            raise ValueError("a regulatory hit must contain at least one record")
        if self.state is not RegulatoryScreeningState.HIT and self.records:
            raise ValueError("only a regulatory hit may contain records")
        if self.state is RegulatoryScreeningState.SCREENING_UNAVAILABLE:
            if self.unavailable_reason is None:
                raise ValueError("screening_unavailable requires a reason")
        elif self.unavailable_reason is not None:
            raise ValueError("unavailable_reason is only valid for unavailable results")
        return self


class RegulatoryScreeningBundle(BaseModel):
    """Both independent regulator results captured by the supply branch."""

    model_config = ConfigDict(extra="forbid")

    eudragmdp: RegulatoryScreeningResult
    openfda: RegulatoryScreeningResult
    advisory_only: Literal[True] = True


class ScreeningAdvisoryFinding(BaseModel):
    """Deterministic presentation finding kept outside GMP compliance findings."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    finding_type: str = "regulatory_screening_advisory"
    source: RegulatoryScreeningSource
    state: RegulatoryScreeningState
    title: str
    description: str
    record_ids: list[str] = Field(default_factory=list)
    source_url: str
    retrieved_at: datetime.datetime
    cache_status: ScreeningCacheStatus
    advisory_only: Literal[True] = True
