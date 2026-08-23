#!/usr/bin/env python3
"""Verify Agent Runtime telemetry for one bounded live invocation.

The case Runtime's Agent Observability spans currently expose the Runtime
resource and workflow/timing metadata, but not the ADK invocation_id. This
verifier therefore correlates a canary only by the exact Runtime resource and
a narrow, measured time window. It fails closed on zero or ambiguous trace
candidates and reports the correlation limitation instead of guessing a span
label or using case content.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import google.auth
import google.auth.transport.requests

DEFAULT_METADATA = Path(__file__).resolve().parents[2] / "case_deployment_metadata.json"
TRACE_API = "https://cloudtrace.googleapis.com/v1"
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
TRACE_WAIT_SECONDS = 90
TRACE_POLL_SECONDS = 5


class VerificationError(RuntimeError):
    """The live observability proof did not meet its acceptance criteria."""


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _request(session: Any, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    response = session.request(method, url, timeout=30, **kwargs)
    if response.status_code >= 400:
        raise VerificationError(
            f"observability API returned HTTP {response.status_code} for {method} {url}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise VerificationError(f"observability API returned non-JSON for {url}") from exc
    if not isinstance(payload, dict):
        raise VerificationError(f"observability API returned a non-object for {url}")
    return payload


def _walk_invocation_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"invocation_id", "invocationId"} and isinstance(child, str):
                found.add(child)
            found.update(_walk_invocation_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_walk_invocation_ids(child))
    return found


def _load_metadata(path: Path) -> dict[str, Any]:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read deployment metadata: {path}") from exc
    required = {
        "case_agent_runtime_id",
        "deployment_target",
        "agent_gateway",
        "project_number",
    }
    missing = sorted(required - metadata.keys())
    if missing:
        raise VerificationError(f"deployment metadata is missing: {', '.join(missing)}")
    if metadata["deployment_target"] != "agent_runtime":
        raise VerificationError("deployment metadata is not an Agent Runtime deployment")
    return metadata


def _authorized_session() -> Any:
    credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
    return google.auth.transport.requests.AuthorizedSession(credentials)


def _runtime_endpoint(runtime_resource: str) -> str:
    try:
        location = runtime_resource.split("/locations/", 1)[1].split("/", 1)[0]
    except (IndexError, AttributeError) as exc:
        raise VerificationError("invalid case Runtime resource in deployment metadata") from exc
    return (
        f"https://{location}-aiplatform.googleapis.com/v1beta1/"
        f"{runtime_resource}:streamQuery?alt=sse"
    )


def _canary(session: Any, runtime_resource: str) -> dict[str, Any]:
    started = datetime.now(UTC)
    response = session.post(
        _runtime_endpoint(runtime_resource),
        json={
            "class_method": "stream_query",
            "input": {
                "user_id": "p4-observability-canary",
                "message": "P4 observability canary; invalid input is intentional.",
            },
        },
        stream=True,
        timeout=(30, 180),
    )
    invocation_ids: set[str] = set()
    line_count = 0
    try:
        if response.status_code >= 400:
            raise VerificationError(
                f"case Runtime canary returned HTTP {response.status_code}"
            )
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line_count += 1
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            invocation_ids.update(_walk_invocation_ids(event))
    finally:
        response.close()
    finished = datetime.now(UTC)
    if len(invocation_ids) != 1:
        raise VerificationError(
            f"expected one canary invocation ID, found {len(invocation_ids)}"
        )
    return {
        "http_status": response.status_code,
        "line_count": line_count,
        "invocation_id": next(iter(invocation_ids)),
        "started_at": _iso(started),
        "finished_at": _iso(finished),
    }


def _trace_ids(
    session: Any, project_number: str, start: datetime, end: datetime
) -> list[str]:
    """All trace IDs in ``[start, end]``, following pagination.

    Cloud Trace v1 ``list`` can return a near-empty first page with a
    ``nextPageToken`` even when more matching traces exist, so a caller that
    only reads page one will silently miss the canary's own trace.
    """
    trace_ids: list[str] = []
    params: dict[str, str] = {
        "pageSize": "100",
        "startTime": _iso(start),
        "endTime": _iso(end),
    }
    for _ in range(20):  # bounded: this is a narrow canary window, not a scan
        payload = _request(
            session,
            "GET",
            f"{TRACE_API}/projects/{project_number}/traces",
            params=params,
        )
        traces = payload.get("traces", [])
        if not isinstance(traces, list):
            raise VerificationError("Cloud Trace list response has an invalid traces field")
        trace_ids.extend(
            trace["traceId"] for trace in traces if isinstance(trace, dict) and trace.get("traceId")
        )
        next_token = payload.get("nextPageToken")
        if not next_token:
            break
        params["pageToken"] = next_token
    return trace_ids


def _trace(session: Any, project_number: str, trace_id: str) -> dict[str, Any]:
    return _request(
        session,
        "GET",
        f"{TRACE_API}/projects/{project_number}/traces/{trace_id}",
    )


def _span_time(span: dict[str, Any], key: str) -> datetime | None:
    value = span.get(key)
    if not isinstance(value, str):
        return None
    try:
        return _parse_time(value)
    except ValueError:
        return None


def _matches_runtime(span: dict[str, Any], runtime_resource: str) -> bool:
    """True if ``span`` was emitted by exactly this Runtime resource.

    ``cloud.resource_id`` observed live is
    ``//aiplatform.googleapis.com/projects/{project_id}/locations/{loc}/reasoningEngines/{id}``
    — a *project id*-qualified path, distinct from the *project number*
    ``runtime_resource`` string used to invoke ``:streamQuery``. Match on the
    trailing ``reasoningEngines/{id}`` segment plus ``service.name`` (the bare
    engine id) rather than a full-string comparison of two different
    project-identifier forms.
    """
    labels = span.get("labels")
    if not isinstance(labels, dict):
        return False
    resource_id = labels.get("cloud.resource_id", "")
    service_name = labels.get("service.name", "")
    engine_id = runtime_resource.rsplit("/", 1)[-1]
    return (
        isinstance(resource_id, str)
        and resource_id.endswith(f"/reasoningEngines/{engine_id}")
    ) or service_name == engine_id


def _find_trace(
    session: Any,
    project_number: str,
    runtime_resource: str,
    started: datetime,
    finished: datetime,
) -> dict[str, Any]:
    # Agent Observability export is eventually consistent. The upper bound is
    # intentionally small: a broad window could associate a different case.
    window_start = started - timedelta(seconds=10)
    window_end = finished + timedelta(seconds=30)
    deadline = time.monotonic() + TRACE_WAIT_SECONDS
    candidates: list[dict[str, Any]] = []
    while time.monotonic() <= deadline:
        candidates = []
        for trace_id in _trace_ids(session, project_number, window_start, window_end):
            trace = _trace(session, project_number, trace_id)
            matching_spans = []
            for span in trace.get("spans", []):
                if not isinstance(span, dict) or not _matches_runtime(span, runtime_resource):
                    continue
                span_start = _span_time(span, "startTime")
                span_end = _span_time(span, "endTime") or span_start
                if span_start is None or span_end is None:
                    continue
                if span_end >= window_start and span_start <= window_end:
                    matching_spans.append(span)
            if matching_spans:
                candidates.append({"trace": trace, "spans": matching_spans})
        if len(candidates) == 1:
            break
        if len(candidates) > 1:
            break
        time.sleep(TRACE_POLL_SECONDS)

    if len(candidates) != 1:
        raise VerificationError(
            "expected exactly one Runtime trace in the canary window; "
            f"found {len(candidates)}"
        )
    candidate = candidates[0]
    trace = candidate["trace"]
    spans = candidate["spans"]
    workflow_spans = [span for span in spans if str(span.get("name", "")).startswith("invoke_workflow")]
    if not workflow_spans:
        raise VerificationError("Runtime trace has no invoke_workflow span")
    invocation_ids = _walk_invocation_ids(trace)
    return {
        "trace_id": trace.get("traceId"),
        "span_count": len(trace.get("spans", [])),
        "runtime_span_count": len(spans),
        "workflow_spans": [
            {
                "name": span.get("name"),
                "start_time": span.get("startTime"),
                "end_time": span.get("endTime"),
                "label_keys": sorted((span.get("labels") or {}).keys()),
            }
            for span in workflow_spans
        ],
        "invocation_id_observed_in_trace": bool(invocation_ids),
        "correlation": "runtime_resource_and_bounded_time_window",
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
    }


def verify(metadata_path: Path) -> dict[str, Any]:
    metadata = _load_metadata(metadata_path)
    session = _authorized_session()
    canary = _canary(session, metadata["case_agent_runtime_id"])
    trace = _find_trace(
        session,
        str(metadata["project_number"]),
        metadata["case_agent_runtime_id"],
        _parse_time(canary["started_at"]),
        _parse_time(canary["finished_at"]),
    )
    return {
        "status": "ok",
        "runtime_resource": metadata["case_agent_runtime_id"],
        "agent_gateway": metadata["agent_gateway"],
        "canary": canary,
        "trace": trace,
        "honesty_note": (
            "The exported Agent Observability span did not expose invocation_id; "
            "correlation is limited to the exact Runtime resource and bounded canary window."
            if not trace["invocation_id_observed_in_trace"]
            else "The exported trace contained invocation_id metadata."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.metadata), indent=2, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
