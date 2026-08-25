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

"""Production Google Calendar adapter for CFI site reviews.

The department MCP owns the Calendar credential. Tenant calendar and
attendee policy are server configuration; neither can be supplied by the
CFI model. The event id is deterministic from ``cfi_id`` and the adapter
looks it up before inserting, so an ADK resume or HTTP retry reuses one
site-review event instead of creating duplicates.
"""

from __future__ import annotations

import datetime
import functools
import hashlib
import json
import os
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.app_utils.services import (
    get_delegated_google_credentials,
    get_dwd_subject,
    get_google_credentials,
)
from app.domain.cfi_artifact_models import CfiSiteReviewReference

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_MAP_ENV = "COUNTERSIGN_CFI_CALENDAR_MAP_JSON"
ATTENDEE_MAP_ENV = "COUNTERSIGN_CFI_CALENDAR_ATTENDEE_MAP_JSON"

# Google Calendar refuses attendee invitations from a bare service-account
# identity ("Service accounts cannot invite attendees without Domain-Wide
# Delegation of Authority"). A site review with no invited attendees is a
# weaker GMP record than the SOP's own section 4.1.3 meeting, so the approved
# attendee list is delivered by acting as a real Workspace user through
# domain-wide delegation instead of silently dropping the invitations.
#
# The subject is server configuration, exactly like the calendar and attendee
# maps: the CFI Runtime and the model cannot choose whose authority is used.
CALENDAR_SUBJECT_ENV = "COUNTERSIGN_CALENDAR_DWD_SUBJECT"

# Optional explicit signer identity. In production the department MCP's own
# Cloud Run service account signs for itself; this override exists so an
# operator probe can sign as that same service account from a workstation.
CALENDAR_SIGNER_ENV = "COUNTERSIGN_CALENDAR_DWD_SIGNER"


class CfiCalendarError(RuntimeError):
    """Calendar is unavailable or returned an invalid site-review event."""


def _required_json_object(name: str) -> dict[str, Any]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise CfiCalendarError(
            f"{name} is required for production CFI Calendar access; "
            "refusing to use an unscoped/default calendar policy."
        )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CfiCalendarError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict) or not value:
        raise CfiCalendarError(f"{name} must be a non-empty JSON object.")
    return value


def calendar_for_tenant(tenant_id: str) -> str:
    mapping = _required_json_object(CALENDAR_MAP_ENV)
    calendar_id = mapping.get(tenant_id)
    if not isinstance(calendar_id, str) or not calendar_id.strip():
        raise CfiCalendarError(
            f"{CALENDAR_MAP_ENV} has no calendar for tenant {tenant_id!r}."
        )
    return calendar_id.strip()


def attendees_for_tenant(tenant_id: str) -> tuple[str, ...]:
    raw = os.environ.get(ATTENDEE_MAP_ENV, "").strip()
    if not raw:
        return ()
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CfiCalendarError(f"{ATTENDEE_MAP_ENV} is not valid JSON: {exc}") from exc
    if not isinstance(mapping, dict):
        raise CfiCalendarError(f"{ATTENDEE_MAP_ENV} must be a JSON object.")
    values = mapping.get(tenant_id, [])
    if not isinstance(values, list) or not all(
        isinstance(item, str) and item.strip() for item in values
    ):
        raise CfiCalendarError(
            f"{ATTENDEE_MAP_ENV} has invalid attendees for tenant {tenant_id!r}."
        )
    return tuple(dict.fromkeys(item.strip() for item in values))


def site_review_event_id(cfi_id: str) -> str:
    """Return a Calendar-safe deterministic id for one CFI."""
    return "cfi" + hashlib.sha256(cfi_id.encode("utf-8")).hexdigest()[:40]


def _rfc3339(value: datetime.datetime) -> str:
    if value.tzinfo is None:
        raise CfiCalendarError("CFI site-review timestamps must include a timezone.")
    return value.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")


def _parse_instant(value: object) -> datetime.datetime | None:
    """Normalize a Calendar ``dateTime`` string to a UTC instant."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.UTC)


def site_review_window(closed_at: datetime.datetime) -> tuple[datetime.datetime, datetime.datetime]:
    """Compute one stable site-review window from the durable close time."""
    raw_delay = os.environ.get("COUNTERSIGN_CFI_SITE_REVIEW_DELAY_HOURS", "24")
    raw_duration = os.environ.get("COUNTERSIGN_CFI_SITE_REVIEW_DURATION_MINUTES", "60")
    try:
        delay_hours = int(raw_delay)
        duration_minutes = int(raw_duration)
    except ValueError as exc:
        raise CfiCalendarError(
            "CFI site-review delay/duration configuration must be integers."
        ) from exc
    if not 1 <= delay_hours <= 24 * 30:
        raise CfiCalendarError(
            "COUNTERSIGN_CFI_SITE_REVIEW_DELAY_HOURS must be between 1 and 720."
        )
    if not 15 <= duration_minutes <= 480:
        raise CfiCalendarError(
            "COUNTERSIGN_CFI_SITE_REVIEW_DURATION_MINUTES must be between 15 and 480."
        )
    # Calendar persists event dateTime values at whole-second precision. Normalize
    # the server-owned window before both the insert and the retry comparison;
    # otherwise a close timestamp with microseconds makes a successful first
    # insert look like a moved event on the next idempotent retry.
    start = (
        closed_at.astimezone(datetime.UTC)
        + datetime.timedelta(hours=delay_hours)
    ).replace(microsecond=0)
    return start, start + datetime.timedelta(minutes=duration_minutes)


def calendar_dwd_subject() -> str | None:
    """The Workspace user whose authority sends site-review invitations."""
    return get_dwd_subject()


@functools.cache
def get_calendar_service():
    subject = calendar_dwd_subject()
    if subject:
        credentials = get_delegated_google_credentials(
            scopes=[CALENDAR_SCOPE], subject=subject
        )
    else:
        credentials = get_google_credentials(scopes=[CALENDAR_SCOPE])
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _http_status(exc: HttpError) -> int | None:
    return getattr(exc.resp, "status", None)


def _event_url(calendar_id: str, event_id: str, event: dict[str, Any]) -> str:
    return str(
        event.get("htmlLink")
        or f"https://calendar.google.com/calendar/u/0/r/eventedit/{event_id}"
    )


def schedule_site_review(
    *,
    tenant_id: str,
    cfi_id: str,
    source_case_id: str,
    batch_id: str,
    outcome: str,
    closed_at: datetime.datetime,
) -> CfiSiteReviewReference:
    """Create or return the one deterministic site-review event.

    The department MCP owns the scheduling policy. Callers provide the
    durable close time only; delay, duration, calendar, and attendees are
    server configuration and cannot be selected by the Runtime/model.
    """
    start_at, end_at = site_review_window(closed_at)
    calendar_id = calendar_for_tenant(tenant_id)
    attendees = attendees_for_tenant(tenant_id)
    if attendees and not calendar_dwd_subject():
        # Google would reject this with an opaque "service accounts cannot
        # invite attendees" 403. Name the missing configuration instead, and
        # refuse rather than quietly filing a site review nobody was invited to.
        raise CfiCalendarError(
            f"Tenant {tenant_id!r} has approved site-review attendees but "
            f"{CALENDAR_SUBJECT_ENV} is not set. Configure domain-wide "
            "delegation for the department MCP service account, or remove the "
            "tenant's attendees from "
            f"{ATTENDEE_MAP_ENV}."
        )
    start_value = _rfc3339(start_at)
    end_value = _rfc3339(end_at)
    if end_value <= start_value:
        raise CfiCalendarError("CFI site-review end must be after its start.")
    event_id = site_review_event_id(cfi_id)
    private = {
        "countersign_tenant_id": tenant_id,
        "countersign_cfi_id": cfi_id,
        "countersign_source_case_id": source_case_id,
    }
    event_body: dict[str, Any] = {
        "id": event_id,
        "summary": f"CFI Site Review — {tenant_id} / {batch_id}",
        "description": (
            f"Cross-Functional Investigation site review for CFI {cfi_id}.\n"
            f"Source case: {source_case_id}\n"
            f"Outcome: {outcome}\n"
            "This meeting concerns investigation review only; it is not a "
            "batch disposition or release decision. decision_authority: none."
        ),
        "start": {"dateTime": start_value, "timeZone": "UTC"},
        "end": {"dateTime": end_value, "timeZone": "UTC"},
        "extendedProperties": {"private": private},
        "attendees": [{"email": email} for email in attendees],
    }
    service = get_calendar_service()
    try:
        try:
            existing = (
                service.events()
                .get(calendarId=calendar_id, eventId=event_id)
                .execute()
            )
        except HttpError as exc:
            if _http_status(exc) != 404:
                raise
            existing = None

        if existing is None:
            try:
                existing = (
                    service.events()
                    .insert(
                        calendarId=calendar_id,
                        body=event_body,
                        sendUpdates="all" if attendees else "none",
                    )
                    .execute()
                )
            except HttpError as exc:
                if _http_status(exc) != 409:
                    raise
                existing = (
                    service.events()
                    .get(calendarId=calendar_id, eventId=event_id)
                    .execute()
                )

        existing_private = (existing.get("extendedProperties") or {}).get("private") or {}
        if any(existing_private.get(key) != value for key, value in private.items()):
            raise CfiCalendarError(
                f"Calendar event id {event_id!r} is bound to a different CFI."
            )
        # Compare instants, not strings. Calendar echoes times in the
        # calendar's own timezone, so a UTC request on an IST calendar comes
        # back as the same moment with a different offset and a different
        # textual form.
        existing_start = _parse_instant((existing.get("start") or {}).get("dateTime"))
        existing_end = _parse_instant((existing.get("end") or {}).get("dateTime"))
        if existing_start is not None and existing_start != start_at.astimezone(
            datetime.UTC
        ):
            raise CfiCalendarError(
                f"Calendar event id {event_id!r} was reused with a different start."
            )
        if existing_end is not None and existing_end != end_at.astimezone(
            datetime.UTC
        ):
            raise CfiCalendarError(
                f"Calendar event id {event_id!r} was reused with a different end."
            )
    except HttpError as exc:
        detail = getattr(exc, "content", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise CfiCalendarError(
            f"Google Calendar site-review event failed with HTTP "
            f"{_http_status(exc)}: {str(detail)[:1000]}"
        ) from exc

    return CfiSiteReviewReference(
        calendar_id=calendar_id,
        event_id=event_id,
        event_url=_event_url(calendar_id, event_id, existing),
        start_at=start_at.astimezone(datetime.UTC),
        end_at=end_at.astimezone(datetime.UTC),
        attendees=attendees,
    )
