"""Minimal ADK hello agent for Agent Runtime + Agent Gateway governance proof."""

from __future__ import annotations

import datetime
import os
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai import types

# Gemini 3.5+ is served from the global Vertex endpoint (Runtime region separate).
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")

MODEL = os.environ.get("MODEL_NAME", "gemini-3.5-flash")


def get_weather(query: str) -> str:
    """Return simulated weather for a location query."""
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Return the current time for a known city query."""
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz = ZoneInfo("America/Los_Angeles")
        now = datetime.datetime.now(tz)
        return f"The current time for {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"
    return f"Sorry, I don't have timezone information for query: {query}."


def probe_http_get(url: str) -> str:
    """HTTP GET used to exercise Agent Gateway egress governance."""
    if not url.startswith("https://"):
        return "Only https URLs are supported."
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "countersign-hello-runtime/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(256).decode("utf-8", errors="replace")
            return f"status={response.status} body_preview={body!r}"
    except urllib.error.HTTPError as exc:
        return f"http_error status={exc.code} reason={exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return f"request_failed error={type(exc).__name__}: {exc}"


root_agent = Agent(
    name="countersign_hello",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "You are a helpful assistant. When asked to probe a URL, call "
        "probe_http_get exactly once and return the tool result verbatim."
    ),
    tools=[get_weather, get_current_time, probe_http_get],
)
