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

"""Production Google Chat API adapter for CFI interviews.

There is deliberately no synthetic Chat catalog or runtime fallback here.
The department MCP either has a real Google Chat configuration and ADC
credentials or fails closed. Unit tests replace the API adapter at the
boundary; they do not change production behavior.
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

from app.app_utils.services import get_google_credentials

CHAT_SEND_SCOPE = "https://www.googleapis.com/auth/chat.bot"
CHAT_READ_SCOPE = "https://www.googleapis.com/auth/chat.app.messages.readonly"
SPACE_MAP_ENV = "COUNTERSIGN_CFI_CHAT_SPACE_MAP_JSON"
RESPONDER_MAP_ENV = "COUNTERSIGN_CFI_CHAT_RESPONDER_MAP_JSON"
MAX_CHAT_PAGES_ENV = "COUNTERSIGN_CFI_CHAT_MAX_PAGES"


class CfiChatError(RuntimeError):
    """Google Chat is unavailable or returned an invalid resource."""


def _required_json_object(name: str) -> dict[str, Any]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise CfiChatError(
            f"{name} is required for production CFI Chat access; refusing "
            "to use an unscoped/default space mapping."
        )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CfiChatError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict) or not value:
        raise CfiChatError(f"{name} must be a non-empty JSON object.")
    return value


def space_for_tenant(tenant_id: str) -> str:
    mapping = _required_json_object(SPACE_MAP_ENV)
    space = mapping.get(tenant_id)
    if not isinstance(space, str) or not space.startswith("spaces/"):
        raise CfiChatError(
            f"{SPACE_MAP_ENV} has no valid named Chat space for tenant "
            f"{tenant_id!r}."
        )
    return space


def responders_for_tenant(tenant_id: str) -> tuple[str, ...]:
    mapping = _required_json_object(RESPONDER_MAP_ENV)
    values = mapping.get(tenant_id)
    if not isinstance(values, list) or not values or not all(
        isinstance(item, str) and item.strip() for item in values
    ):
        raise CfiChatError(
            f"{RESPONDER_MAP_ENV} has no non-empty responder resource-name "
            f"allow-list for tenant {tenant_id!r}."
        )
    return tuple(dict.fromkeys(item.strip() for item in values))


def max_chat_pages() -> int:
    raw = os.environ.get(MAX_CHAT_PAGES_ENV, "").strip()
    if not raw:
        raise CfiChatError(
            f"{MAX_CHAT_PAGES_ENV} is required; unbounded Chat history reads "
            "are not permitted."
        )
    try:
        value = int(raw)
    except ValueError as exc:
        raise CfiChatError(f"{MAX_CHAT_PAGES_ENV} must be an integer.") from exc
    if value < 1 or value > 100:
        raise CfiChatError(f"{MAX_CHAT_PAGES_ENV} must be between 1 and 100.")
    return value


@functools.cache
def get_chat_service():
    """Build the real Chat API client from the MCP service identity's ADC."""
    credentials = get_google_credentials(scopes=[CHAT_SEND_SCOPE, CHAT_READ_SCOPE])
    return build("chat", "v1", credentials=credentials, cache_discovery=False)


def _custom_message_id(action_key: str) -> str:
    # Chat requires a client- prefix, lowercase letters/numbers/hyphens, and
    # at most 63 characters. The full action key remains in Cloud SQL; this
    # short digest is only the Chat idempotency handle.
    return f"client-{hashlib.sha256(action_key.encode('utf-8')).hexdigest()[:48]}"


def _request_error(exc: HttpError, operation: str) -> CfiChatError:
    status = getattr(exc.resp, "status", "unknown")
    detail = getattr(exc, "content", b"")
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", errors="replace")
    return CfiChatError(
        f"Google Chat {operation} failed with HTTP {status}: {str(detail)[:1000]}"
    )


def send_message(
    *,
    space_name: str,
    thread_key: str | None,
    thread_name: str | None = None,
    text: str,
    action_key: str,
) -> dict[str, Any]:
    """Send one idempotent app-authenticated Chat message.

    ``requestId`` and the client-assigned ``messageId`` are deterministic for
    the same action. The thread uses the current Chat ``thread.threadKey``
    request field, and OR_FAIL prevents an unexpected new thread if a stored
    thread is supposed to exist.
    """
    if not text.strip():
        raise CfiChatError("Cannot send an empty CFI interview message.")
    if len(text.encode("utf-8")) > 30_000:
        raise CfiChatError("CFI interview message exceeds the safe Chat size limit.")
    if not space_name.startswith("spaces/"):
        raise CfiChatError(f"Invalid Chat space resource name: {space_name!r}")
    if not thread_key and not thread_name:
        raise CfiChatError("CFI interview requires a thread key or thread name.")

    custom_id = _custom_message_id(action_key)
    body = {"text": text}
    if thread_name:
        body["thread"] = {"name": thread_name}
    else:
        body["thread"] = {"threadKey": thread_key}
    service = get_chat_service()
    try:
        return (
            service.spaces()
            .messages()
            .create(
                parent=space_name,
                body=body,
                messageId=custom_id,
                requestId=custom_id,
                messageReplyOption="REPLY_MESSAGE_OR_FAIL",
            )
            .execute()
        )
    except HttpError as exc:
        # A retry can race the first request after Chat committed the message.
        # Resolve only the documented idempotent custom id; never send a new
        # message with a different identifier.
        if getattr(exc.resp, "status", None) in (409, 412):
            try:
                return (
                    service.spaces()
                    .messages()
                    .get(name=f"{space_name}/messages/{custom_id}")
                    .execute()
                )
            except HttpError as get_exc:
                raise _request_error(get_exc, "idempotent message lookup") from get_exc
        raise _request_error(exc, "message creation") from exc


def list_thread_messages(
    *,
    space_name: str,
    thread_name: str,
    after: datetime.datetime | None,
) -> list[dict[str, Any]]:
    """Read a bounded, paginated Chat thread using the official filter syntax."""
    if not space_name.startswith("spaces/"):
        raise CfiChatError(f"Invalid Chat space resource name: {space_name!r}")
    if not thread_name.startswith(f"{space_name}/threads/"):
        raise CfiChatError(
            f"Thread {thread_name!r} does not belong to space {space_name!r}."
        )

    filters = []
    if after is not None:
        timestamp = after.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")
        filters.append(f'create_time > "{timestamp}"')
    filters.append(f"thread.name = {thread_name}")
    message_filter = " AND ".join(filters)

    service = get_chat_service()
    page_token: str | None = None
    seen_tokens: set[str] = set()
    messages: list[dict[str, Any]] = []
    pages = 0
    while True:
        pages += 1
        if pages > max_chat_pages():
            raise CfiChatError(
                f"Chat thread exceeded {max_chat_pages()} pages; refusing an "
                "unbounded read."
            )
        try:
            response = (
                service.spaces()
                .messages()
                .list(
                    parent=space_name,
                    filter=message_filter,
                    orderBy="create_time ASC",
                    pageSize=1000,
                    pageToken=page_token,
                )
                .execute()
            )
        except HttpError as exc:
            raise _request_error(exc, "thread read") from exc
        messages.extend(response.get("messages", []))
        next_token = response.get("nextPageToken")
        if not next_token:
            break
        if next_token in seen_tokens:
            raise CfiChatError("Google Chat returned a repeated page token.")
        seen_tokens.add(next_token)
        page_token = next_token
    return messages


def message_timestamp(message: dict[str, Any]) -> datetime.datetime | None:
    raw = message.get("createTime")
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def message_sender_id(message: dict[str, Any]) -> str | None:
    sender = message.get("sender") or {}
    value = sender.get("name")
    return value if isinstance(value, str) and value else None


def message_is_bot(message: dict[str, Any]) -> bool:
    sender = message.get("sender") or {}
    return sender.get("type") == "BOT"


def message_text(message: dict[str, Any]) -> str:
    value = message.get("text")
    return value if isinstance(value, str) else ""


def _required_positive_int(name: str, *, maximum: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise CfiChatError(f"{name} is required for production CFI interviews.")
    try:
        value = int(raw)
    except ValueError as exc:
        raise CfiChatError(f"{name} must be an integer.") from exc
    if value <= 0 or (maximum is not None and value > maximum):
        bound = f" and <= {maximum}" if maximum is not None else ""
        raise CfiChatError(f"{name} must be > 0{bound}.")
    return value


def interview_deadline_seconds() -> int:
    return _required_positive_int("COUNTERSIGN_CFI_INTERVIEW_DEADLINE_SECONDS")


def max_nudges() -> int:
    raw = os.environ.get("COUNTERSIGN_CFI_MAX_NUDGES", "").strip()
    if not raw:
        raise CfiChatError("COUNTERSIGN_CFI_MAX_NUDGES is required for production CFI interviews.")
    try:
        value = int(raw)
    except ValueError as exc:
        raise CfiChatError("COUNTERSIGN_CFI_MAX_NUDGES must be an integer.") from exc
    if value < 0 or value > 10:
        raise CfiChatError("COUNTERSIGN_CFI_MAX_NUDGES must be between 0 and 10.")
    return value


def nudge_interval_seconds() -> int:
    return _required_positive_int("COUNTERSIGN_CFI_NUDGE_INTERVAL_SECONDS")
