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

"""Real QC lab system: assay results read from a live SENAITE LIMS instance.

Item 5 of ``final_reseach/12_enhancement.md``. SENAITE is a genuine,
ISO/IEC 17025-oriented LIMS: samples are ``AnalysisRequest`` objects, each
carrying one or more ``Analysis`` child records with a result, unit, and a
per-analysis specification range. QA/lab staff enter this data themselves
through the SENAITE web UI (or an instrument-import path) exactly as a real
CDMO would; this module only ever issues read-only GET requests against
``senaite.jsonapi`` — it does not create, seed, or fabricate any sample,
result, or client.

Runs inside the department MCP process only, mirroring the Cloud SQL LIMS
source: the base URL and admin credential are read from Secret
Manager/environment in this process, never handed to an agent or exposed to
a caller-supplied filter. Tenant isolation maps our ``tenant_id`` onto a
SENAITE ``Client``'s ``ClientID`` and every query is scoped by it.

**Known constraint (verified against senaite.jsonapi 2.6.0):** creating an
``AnalysisRequest`` through the JSON API is unsupported
(https://github.com/senaite/senaite.jsonapi/issues/31); this class does not
attempt writes, so that gap does not block the read integration.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.domain.department_models import LimsResultRow, LimsSnapshot
from app.domain.department_source import BACKEND_SENAITE, DepartmentRecordMissing

SENAITE_URL_ENV = "COUNTERSIGN_SENAITE_URL"
SENAITE_USERNAME_ENV = "COUNTERSIGN_SENAITE_USERNAME"
SENAITE_PASSWORD_ENV = "COUNTERSIGN_SENAITE_PASSWORD"

_API_PATH = "@@API/senaite/v1"
_DEFAULT_HTTP_TIMEOUT_SECONDS = 15.0


def _http_timeout_seconds() -> float:
    raw = os.environ.get("COUNTERSIGN_SENAITE_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_HTTP_TIMEOUT_SECONDS
    return float(raw)


def senaite_base_url() -> str:
    url = os.environ.get(SENAITE_URL_ENV, "").strip()
    if not url:
        raise RuntimeError(f"{SENAITE_URL_ENV} is not set.")
    return url.rstrip("/")


def _senaite_credentials() -> tuple[str, str]:
    username = os.environ.get(SENAITE_USERNAME_ENV, "").strip()
    password = os.environ.get(SENAITE_PASSWORD_ENV, "")
    if not username or not password:
        raise RuntimeError(
            f"{SENAITE_USERNAME_ENV}/{SENAITE_PASSWORD_ENV} must both be set."
        )
    return username, password


class SenaiteApiError(Exception):
    """A SENAITE JSON API request failed at the transport/HTTP layer."""


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """Issue a single authenticated GET against ``senaite.jsonapi``.

    Read-only by construction: this module has exactly one HTTP verb, GET.
    No caller in this file can create, update, or delete a SENAITE record —
    matching the ALCOA+ read-only discipline the Cloud SQL LIMS source
    already holds for laboratory results.
    """
    base = senaite_base_url()
    username, password = _senaite_credentials()
    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{base}/{_API_PATH}/{path}?{query}"
    request = urllib.request.Request(url, method="GET")
    credentials = f"{username}:{password}".encode("ascii")
    import base64

    request.add_header(
        "Authorization", f"Basic {base64.b64encode(credentials).decode('ascii')}"
    )
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(
            request, timeout=_http_timeout_seconds()
        ) as response:
            import json

            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SenaiteApiError(
            f"SENAITE API GET {path} failed: HTTP {exc.code}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SenaiteApiError(f"SENAITE API GET {path} failed: {exc}") from exc


def _search(portal_type: str, **params: Any) -> list[dict[str, Any]]:
    result = _get("search", {"portal_type": portal_type, **params})
    items = result.get("items")
    if not isinstance(items, list):
        return []
    return items


def _results_range(analysis: dict[str, Any]) -> tuple[float, float]:
    """SENAITE's ``ResultsRange`` field: a dict with ``min``/``max`` (and
    optional ``warn_min``/``warn_max``, which check_lims() does not use).
    Missing or unparsable bounds fail closed via ``DepartmentRecordMissing``
    rather than silently defaulting a spec range — an assumed spec is a
    worse failure mode than a visibly missing one for a GMP OOS check.
    """
    raw_range = analysis.get("ResultsRange")
    if not isinstance(raw_range, dict):
        return None  # type: ignore[return-value]
    try:
        spec_min = float(raw_range["min"])
        spec_max = float(raw_range["max"])
    except (KeyError, TypeError, ValueError):
        return None  # type: ignore[return-value]
    return spec_min, spec_max


class SenaiteLimsSource:
    """SENAITE-backed QC lab reads over ``senaite.jsonapi``."""

    backend_name = BACKEND_SENAITE

    def source_uri(self, *, tenant_id: str, batch_id: str) -> str:
        """A citable URL naming the exact SENAITE search this row came
        from — the same discipline as ``PostgresLimsSource.source_uri``: a
        citation that cannot be traced back to a system of record is not an
        audit trail.
        """
        base = senaite_base_url()
        query = urllib.parse.urlencode(
            {
                "portal_type": "AnalysisRequest",
                "getClientID": tenant_id,
                "getClientSampleID": batch_id,
            }
        )
        return f"{base}/{_API_PATH}/search?{query}"

    def lims(self, *, tenant_id: str, batch_id: str) -> LimsSnapshot:
        samples = _search(
            "AnalysisRequest",
            getClientID=tenant_id,
            getClientSampleID=batch_id,
        )
        if not samples:
            raise DepartmentRecordMissing(
                f"No SENAITE sample for client {tenant_id!r}, "
                f"batch {batch_id!r}."
            )

        rows: list[LimsResultRow] = []
        # Keyed by (sample_id, test_code): SENAITE keeps a retest/audit
        # history entry for every retract-and-resubmit cycle on an
        # analysis. We only want the latest live result per test per
        # sample, not every historical retest copy, so later rows in the
        # search result (which SENAITE returns in creation order) replace
        # earlier ones for the same key.
        rows_by_key: dict[tuple[str, str], LimsResultRow] = {}
        for sample in samples:
            sample_id = sample.get("id")
            if not sample_id:
                continue
            analyses = _search(
                "Analysis",
                getRequestID=sample_id,
                complete="true",
            )
            for analysis in analyses:
                result_raw = analysis.get("Result")
                if result_raw in (None, ""):
                    # Not yet resulted in SENAITE — not an OOS input, and
                    # not a missing-record error either; just skip it the
                    # way an unfilled worksheet cell has nothing to report.
                    continue
                try:
                    result_value = float(result_raw)
                except (TypeError, ValueError):
                    continue
                spec = _results_range(analysis)
                if spec is None:
                    continue
                spec_min, spec_max = spec
                keyword = analysis.get("Keyword") or analysis.get("getKeyword") or ""
                title = analysis.get("Title") or keyword
                unit = analysis.get("Unit") or analysis.get("getUnit") or ""
                rows_by_key[(str(sample_id), str(keyword))] = LimsResultRow(
                    test_code=str(keyword),
                    label=str(title),
                    result_value=result_value,
                    unit=str(unit),
                    spec_min=spec_min,
                    spec_max=spec_max,
                    sample_id=str(sample_id),
                )
        rows = list(rows_by_key.values())

        if not rows:
            raise DepartmentRecordMissing(
                f"SENAITE sample(s) for {tenant_id!r}/{batch_id!r} carry no "
                "resulted analysis with a specification range yet."
            )

        return LimsSnapshot(
            system=BACKEND_SENAITE,
            source_uri=self.source_uri(tenant_id=tenant_id, batch_id=batch_id),
            rows=rows,
        )
