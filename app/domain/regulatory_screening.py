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

"""Deterministic public regulator clients for T1-7.

The EudraGMDP endpoint is an official HTML search portal, not a documented
machine API. Its response contract is therefore validated before any record is
returned. openFDA is consumed through its official JSON endpoint and its
response shape is validated as well. Neither client has a fallback dataset,
uses stale cache entries, or sends Countersign-internal identifiers upstream.
"""

from __future__ import annotations

import datetime
import hashlib
import html.parser
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.domain.regulatory_screening_models import (
    EudraGmdpQuery,
    OpenFdaEnforcementQuery,
    PublicScreeningQuery,
    RegulatoryScreeningRecord,
    RegulatoryScreeningResult,
    RegulatoryScreeningSource,
    RegulatoryScreeningState,
    ScreeningAdvisoryFinding,
    ScreeningCacheStatus,
    ScreeningProvenance,
    ScreeningUnavailableReason,
)

EUDRAGMDP_NON_COMPLIANCE_URL = (
    "https://eudragmdp.ema.europa.eu/inspections/gmpc/"
    "searchGMPNonCompliance.do"
)
OPENFDA_DRUG_ENFORCEMENT_URL = "https://api.fda.gov/drug/enforcement.json"
EUDRAGMDP_SCREEN = "eudragmdp_screen"
OPENFDA_ENFORCEMENT_SCREEN = "openfda_enforcement_screen"
REGULATORY_SCREENING_TOOLS = frozenset(
    {EUDRAGMDP_SCREEN, OPENFDA_ENFORCEMENT_SCREEN}
)

SCREENING_SCHEMA_VERSION = "t1-7-screening-v1"
EUDRA_PARSER_VERSION = "eudragmdp-noncompliance-html-v1"
OPENFDA_PARSER_VERSION = "openfda-enforcement-json-v1"
SCREENING_CACHE_TTL_ENV = "COUNTERSIGN_REGULATORY_SCREENING_CACHE_TTL_SECONDS"
SCREENING_HTTP_TIMEOUT_ENV = "COUNTERSIGN_REGULATORY_SCREENING_TIMEOUT_SECONDS"
DEFAULT_CACHE_TTL_SECONDS = 900
DEFAULT_HTTP_TIMEOUT_SECONDS = 15
MAX_CACHE_ENTRIES = 256
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_EUDRA_PAGES = 50
MAX_OPENFDA_RECORDS = 100
EUDRA_HISTORY_START = datetime.date(2024, 1, 1)
_USER_AGENT = "Countersign-regulatory-screen/1.0"


class RegulatoryScreeningClientError(Exception):
    """Internal error converted to a typed screening_unavailable result."""


class _SchemaChanged(RegulatoryScreeningClientError):
    pass


class _ResponseTooLarge(RegulatoryScreeningClientError):
    pass


@dataclass(frozen=True)
class _HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str


@dataclass(frozen=True)
class _CacheEntry:
    result: RegulatoryScreeningResult
    expires_at: datetime.datetime
    expires_monotonic: float


class _ScreeningCache:
    """Process-local, bounded TTL cache; expired values are never returned."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()

    def get(self, key: str) -> RegulatoryScreeningResult | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if now >= entry.expires_monotonic:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            provenance = entry.result.provenance.model_copy(
                update={"cache_status": ScreeningCacheStatus.CACHE_HIT}
            )
            return entry.result.model_copy(
                deep=True, update={"provenance": provenance}
            )

    def put(
        self,
        key: str,
        result: RegulatoryScreeningResult,
        *,
        ttl_seconds: int,
        now: datetime.datetime,
    ) -> RegulatoryScreeningResult:
        expires_at = now + datetime.timedelta(seconds=ttl_seconds)
        live_provenance = result.provenance.model_copy(
            update={
                "cache_status": ScreeningCacheStatus.LIVE,
                "cache_expires_at": expires_at,
            }
        )
        live_result = result.model_copy(
            deep=True, update={"provenance": live_provenance}
        )
        with self._lock:
            self._entries[key] = _CacheEntry(
                result=live_result,
                expires_at=expires_at,
                expires_monotonic=time.monotonic() + ttl_seconds,
            )
            self._entries.move_to_end(key)
            while len(self._entries) > MAX_CACHE_ENTRIES:
                self._entries.popitem(last=False)
        return live_result

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_CACHE = _ScreeningCache()


def clear_screening_cache() -> None:
    """Clear process-local screening cache; useful for controlled restarts/tests."""
    _CACHE.clear()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _configured_seconds(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RegulatoryScreeningClientError(f"invalid {name}") from exc
    if not minimum <= value <= maximum:
        raise RegulatoryScreeningClientError(f"invalid {name}")
    return value


def _cache_ttl_seconds() -> int:
    return _configured_seconds(
        SCREENING_CACHE_TTL_ENV,
        DEFAULT_CACHE_TTL_SECONDS,
        minimum=60,
        maximum=3600,
    )


def _http_timeout_seconds() -> int:
    return _configured_seconds(
        SCREENING_HTTP_TIMEOUT_ENV,
        DEFAULT_HTTP_TIMEOUT_SECONDS,
        minimum=1,
        maximum=60,
    )


def _safe_query(raw: dict[str, object]) -> PublicScreeningQuery:
    try:
        return PublicScreeningQuery.model_validate(raw)
    except ValidationError as exc:
        raise RegulatoryScreeningClientError("invalid public query") from exc


def _cache_key(
    source: RegulatoryScreeningSource,
    query: PublicScreeningQuery,
    request_parameters: dict[str, str],
) -> str:
    payload = {
        "source": source.value,
        "query": query.model_dump(mode="json"),
        "request_parameters": request_parameters,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _response_hash(body: bytes | None) -> str | None:
    return hashlib.sha256(body).hexdigest() if body is not None else None


def _read_limited(stream: Any) -> bytes:
    body = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise _ResponseTooLarge("upstream response exceeded the size limit")
    return body


def _headers(response: Any) -> dict[str, str]:
    raw_headers = getattr(response, "headers", None)
    if raw_headers is None or not hasattr(raw_headers, "items"):
        return {}
    return {str(key).lower(): str(value) for key, value in raw_headers.items()}


def _response_url(response: Any, fallback: str) -> str:
    geturl = getattr(response, "geturl", None)
    if callable(geturl):
        value = geturl()
        if value:
            return str(value)
    return fallback


def _fetch(request: urllib.request.Request, *, opener: Any = None) -> _HttpResponse:
    open_fn = opener.open if opener is not None else urllib.request.urlopen
    try:
        with open_fn(request, timeout=_http_timeout_seconds()) as response:
            status = int(getattr(response, "status", response.getcode()))
            return _HttpResponse(
                status,
                _headers(response),
                _read_limited(response),
                _response_url(response, request.full_url),
            )
    except urllib.error.HTTPError as exc:
        try:
            body = _read_limited(exc)
        except _ResponseTooLarge:
            raise
        return _HttpResponse(
            exc.code,
            _headers(exc),
            body,
            _response_url(exc, request.full_url),
        )
    except _ResponseTooLarge:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RegulatoryScreeningClientError("upstream transport failure") from exc


def _provenance(
    *,
    source: RegulatoryScreeningSource,
    query: PublicScreeningQuery,
    request_url: str,
    request_method: str,
    request_parameters: dict[str, str],
    response: bytes | None,
    http_status: int | None,
    retrieved_at: datetime.datetime,
    source_updated_at: datetime.datetime | None = None,
    response_truncated: bool = False,
) -> ScreeningProvenance:
    return ScreeningProvenance(
        source=source,
        source_url=(
            EUDRAGMDP_NON_COMPLIANCE_URL
            if source is RegulatoryScreeningSource.EUDRAGMDP
            else OPENFDA_DRUG_ENFORCEMENT_URL
        ),
        request_url=request_url,
        request_method=request_method,  # type: ignore[arg-type]
        query=query,
        request_parameters=request_parameters,
        http_status=http_status,
        retrieved_at=retrieved_at,
        source_updated_at=source_updated_at,
        response_sha256=_response_hash(response),
        parser_version=(
            EUDRA_PARSER_VERSION
            if source is RegulatoryScreeningSource.EUDRAGMDP
            else OPENFDA_PARSER_VERSION
        ),
        schema_version=SCREENING_SCHEMA_VERSION,
        response_truncated=response_truncated,
    )


def _unavailable(
    *,
    source: RegulatoryScreeningSource,
    query: PublicScreeningQuery,
    reason: ScreeningUnavailableReason,
    request_url: str,
    request_method: str,
    request_parameters: dict[str, str],
    response: bytes | None = None,
    http_status: int | None = None,
    retrieved_at: datetime.datetime | None = None,
) -> RegulatoryScreeningResult:
    return RegulatoryScreeningResult(
        source=source,
        state=RegulatoryScreeningState.SCREENING_UNAVAILABLE,
        query=query,
        provenance=_provenance(
            source=source,
            query=query,
            request_url=request_url,
            request_method=request_method,
            request_parameters=request_parameters,
            response=response,
            http_status=http_status,
            retrieved_at=retrieved_at or _now(),
        ),
        unavailable_reason=reason,
    )


def unavailable_screening_result(
    source: RegulatoryScreeningSource,
    *,
    query: PublicScreeningQuery | None = None,
    reason: ScreeningUnavailableReason | None = None,
) -> RegulatoryScreeningResult:
    """Build an explicit unavailable result when the MCP boundary cannot answer."""
    effective_reason = reason or ScreeningUnavailableReason.MCP_UNAVAILABLE
    return _unavailable(
        source=source,
        query=query or PublicScreeningQuery(),
        reason=effective_reason,
        request_url=(
            EUDRAGMDP_NON_COMPLIANCE_URL
            if source is RegulatoryScreeningSource.EUDRAGMDP
            else OPENFDA_DRUG_ENFORCEMENT_URL
        ),
        request_method=("POST" if source is RegulatoryScreeningSource.EUDRAGMDP else "GET"),
        request_parameters={},
    )


def _make_result(
    *,
    source: RegulatoryScreeningSource,
    state: RegulatoryScreeningState,
    query: PublicScreeningQuery,
    records: list[RegulatoryScreeningRecord],
    request_url: str,
    request_method: str,
    request_parameters: dict[str, str],
    response: bytes,
    http_status: int,
    retrieved_at: datetime.datetime,
    source_updated_at: datetime.datetime | None = None,
    response_truncated: bool = False,
) -> RegulatoryScreeningResult:
    return RegulatoryScreeningResult(
        source=source,
        state=state,
        query=query,
        records=records,
        provenance=_provenance(
            source=source,
            query=query,
            request_url=request_url,
            request_method=request_method,
            request_parameters=request_parameters,
            response=response,
            http_status=http_status,
            retrieved_at=retrieved_at,
            source_updated_at=source_updated_at,
            response_truncated=response_truncated,
        ),
    )


def _cached_or_fetch(
    *,
    source: RegulatoryScreeningSource,
    query: PublicScreeningQuery,
    request_parameters: dict[str, str],
    fetch: Callable[[], RegulatoryScreeningResult],
) -> RegulatoryScreeningResult:
    key = _cache_key(source, query, request_parameters)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    result = fetch()
    if result.state is RegulatoryScreeningState.SCREENING_UNAVAILABLE:
        return result
    return _CACHE.put(
        key,
        result,
        ttl_seconds=_cache_ttl_seconds(),
        now=result.provenance.retrieved_at,
    )


def _date_value(value: str, *, required: bool = False) -> datetime.datetime | None:
    normalized = value.strip()
    if not normalized:
        if required:
            raise _SchemaChanged("required upstream date missing")
        return None
    try:
        if re.fullmatch(r"\d{8}", normalized):
            parsed = datetime.date.fromisoformat(
                f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:8]}"
            )
        else:
            parsed = datetime.date.fromisoformat(normalized[:10])
    except ValueError as exc:
        raise _SchemaChanged("upstream date field changed") from exc
    return datetime.datetime.combine(parsed, datetime.time(), tzinfo=datetime.UTC)


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalized_tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _normalized_text(value)))


@dataclass(frozen=True)
class _HtmlCell:
    text: str
    href: str | None = None


@dataclass(frozen=True)
class _HtmlRow:
    css_class: str
    cells: tuple[_HtmlCell, ...]


@dataclass
class _MutableHtmlRow:
    css_class: str
    cells: list[_HtmlCell]
    cell_text: list[str] | None = None
    cell_href: str | None = None


class _EudraHtmlParser(html.parser.HTMLParser):
    """Extract only the published result-table rows and pagination links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_HtmlRow] = []
        self.links: list[str] = []
        self._row_stack: list[_MutableHtmlRow] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag == "tr":
            self._row_stack.append(
                _MutableHtmlRow(
                    css_class=(attrs_map.get("class") or "").casefold(),
                    cells=[],
                )
            )
            return
        if tag == "td" and self._row_stack:
            row = self._row_stack[-1]
            row.cell_text = []
            row.cell_href = None
            return
        if tag == "a":
            href = attrs_map.get("href")
            if href:
                # The current portal emits ``&param`` without escaping the
                # ampersand; HTMLParser decodes the ``&para`` entity to U+00B6.
                href = href.replace("\u00b6m=", "&param=")
                self.links.append(href)
                if self._row_stack and self._row_stack[-1].cell_text is not None:
                    self._row_stack[-1].cell_href = href

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._row_stack:
            row = self._row_stack[-1]
            if row.cell_text is not None:
                row.cells.append(
                    _HtmlCell(
                        text=" ".join("".join(row.cell_text).split()),
                        href=row.cell_href,
                    )
                )
                row.cell_text = None
                row.cell_href = None
            return
        if tag == "tr" and self._row_stack:
            row = self._row_stack.pop()
            self.rows.append(_HtmlRow(row.css_class, tuple(row.cells)))

    def handle_data(self, data: str) -> None:
        if self._row_stack and self._row_stack[-1].cell_text is not None:
            self._row_stack[-1].cell_text.append(data)


_EUDRA_HEADERS = (
    "report number",
    "eudragmdp document reference number",
    "mia number",
    "site name",
    "site address",
    "oms location identifier",
    "city",
    "postcode",
    "country",
    "inspection end date",
    "issue date",
)


def _validated_eudra_url(url: str, *, pagination: bool) -> str:
    """Allow only the official Eudra portal origin and search path."""
    candidate = urllib.parse.urljoin(EUDRAGMDP_NON_COMPLIANCE_URL, url)
    expected = urllib.parse.urlsplit(EUDRAGMDP_NON_COMPLIANCE_URL)
    actual = urllib.parse.urlsplit(candidate)
    allowed_session_path = re.fullmatch(
        rf"{re.escape(expected.path)};jsessionid=[A-Za-z0-9!_~.\-]+",
        actual.path,
    )
    if (
        actual.scheme != expected.scheme
        or actual.netloc != expected.netloc
        or (actual.path != expected.path and allowed_session_path is None)
        or actual.fragment
    ):
        raise _SchemaChanged("EudraGMDP pagination destination changed")
    if pagination:
        parameters = urllib.parse.parse_qs(actual.query, keep_blank_values=True)
        page_values = parameters.get("param") or []
        if (
            parameters.get("ctrl") != ["searchGMPNCResultControlList"]
            or parameters.get("action") != ["Page"]
            or len(page_values) != 1
        ):
            raise _SchemaChanged("EudraGMDP pagination query changed")
        if re.fullmatch(r"-?\d+", page_values[0]) is None:
            raise _SchemaChanged("EudraGMDP pagination parameter changed")
    return candidate


class _EudraRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validated_eudra_url(newurl, pagination=False)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validated_openfda_url(url: str, *, expected_query: str) -> str:
    """Allow only the official openFDA endpoint and the original query."""
    candidate = urllib.parse.urljoin(OPENFDA_DRUG_ENFORCEMENT_URL, url)
    expected = urllib.parse.urlsplit(OPENFDA_DRUG_ENFORCEMENT_URL)
    actual = urllib.parse.urlsplit(candidate)
    if (
        actual.scheme != expected.scheme
        or actual.netloc != expected.netloc
        or actual.path != expected.path
        or actual.query != expected_query
        or actual.fragment
    ):
        raise _SchemaChanged("openFDA redirect destination changed")
    return candidate


class _OpenFdaRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validated_openfda_url(
            newurl,
            expected_query=urllib.parse.urlsplit(req.full_url).query,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _eudra_page(
    body: bytes,
) -> tuple[list[tuple[_HtmlCell, ...]], str | None, int | None]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _SchemaChanged("EudraGMDP response was not UTF-8 HTML") from exc
    if "GMP Non-Compliance Search Results" not in text:
        raise _SchemaChanged("EudraGMDP result marker is missing")
    parser = _EudraHtmlParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise _SchemaChanged("EudraGMDP HTML could not be parsed") from exc

    header_rows = [
        row
        for row in parser.rows
        if row.css_class == "header"
        and tuple(_normalized_text(cell.text) for cell in row.cells) == _EUDRA_HEADERS
    ]
    if len(header_rows) != 1:
        raise _SchemaChanged("EudraGMDP result-table headers changed")

    data_rows: list[tuple[_HtmlCell, ...]] = []
    for row in parser.rows:
        if row.css_class not in {"even", "odd"}:
            continue
        if len(row.cells) == 1 and not row.cells[0].text:
            continue
        if len(row.cells) != len(_EUDRA_HEADERS):
            raise _SchemaChanged("EudraGMDP result-table columns changed")
        data_rows.append(row.cells)

    total_match = re.search(r"\b\d+\s+to\s+\d+\s+of\s+(\d+)\b", text)
    if total_match is None:
        raise _SchemaChanged("EudraGMDP result total marker is missing")
    total = int(total_match.group(1))
    next_links = [
        href
        for href in parser.links
        if "action=Page" in href and "param=" in href
    ]
    def page_number(href: str) -> int:
        match = re.search(r"param=(-?\d+)", href)
        if match is None:
            raise _SchemaChanged("EudraGMDP pagination link changed")
        return int(match.group(1))

    next_href = max(next_links, key=page_number) if next_links else None
    return data_rows, next_href, total


def _eudra_record(
    cells: tuple[_HtmlCell, ...],
) -> RegulatoryScreeningRecord:
    values = [cell.text for cell in cells]
    record_id = f"{values[0].strip()}#{values[1].strip()}"
    if not record_id or not values[1].strip() or not values[3].strip():
        raise _SchemaChanged("EudraGMDP record identity fields are missing")
    return RegulatoryScreeningRecord(
        record_id=record_id,
        source_url=EUDRAGMDP_NON_COMPLIANCE_URL,
        site_name=values[3].strip(),
        site_address=values[4].strip() or None,
        oms_location_id=values[5].strip() or None,
        city=values[6].strip() or None,
        country=values[8].strip() or None,
        source_updated_at=_date_value(values[10], required=True),
    )


def _eudra_matches(
    record: RegulatoryScreeningRecord, query: EudraGmdpQuery
) -> bool:
    assert query.manufacturer_name is not None
    assert query.country is not None
    record_name = _normalized_tokens(record.site_name or "")
    requested_name = _normalized_tokens(query.manufacturer_name)
    if not requested_name.issubset(record_name):
        return False
    if _normalized_text(record.country or "") != _normalized_text(query.country):
        return False
    if query.city and _normalized_text(record.city or "") != _normalized_text(query.city):
        return False
    if query.oms_location_id and record.oms_location_id != query.oms_location_id:
        return False
    return True


def _eudra_request_parameters(today: datetime.date) -> dict[str, str]:
    return {
        "formid": "frmGMPCSearch",
        "fromDate": EUDRA_HISTORY_START.isoformat(),
        "toDate": today.isoformat(),
        "btnSearchGMPNC": "clicked",
    }


def _screen_eudragmdp_live(
    query: EudraGmdpQuery,
    *,
    request_parameters: dict[str, str],
) -> RegulatoryScreeningResult:
    retrieved_at = _now()
    body = urllib.parse.urlencode(request_parameters).encode("ascii")
    request = urllib.request.Request(
        EUDRAGMDP_NON_COMPLIANCE_URL,
        data=body,
        method="POST",
        headers={
            "Accept": "text/html",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _USER_AGENT,
        },
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(), _EudraRedirectHandler()
    )
    page_bodies: list[bytes] = []
    all_records: list[RegulatoryScreeningRecord] = []
    next_url: str | None = EUDRAGMDP_NON_COMPLIANCE_URL
    total: int | None = None
    page_number = 0

    while next_url is not None:
        page_number += 1
        if page_number > MAX_EUDRA_PAGES:
            raise _ResponseTooLarge("EudraGMDP pagination exceeded the bound")
        if page_number == 1:
            response = _fetch(request, opener=opener)
        else:
            page_request = urllib.request.Request(
                next_url,
                method="GET",
                headers={"Accept": "text/html", "User-Agent": _USER_AGENT},
            )
            response = _fetch(page_request, opener=opener)
        if response.status < 200 or response.status >= 300:
            reason = (
                ScreeningUnavailableReason.UPSTREAM_RATE_LIMITED
                if response.status == 429
                else ScreeningUnavailableReason.UPSTREAM_HTTP_ERROR
            )
            return _unavailable(
                source=RegulatoryScreeningSource.EUDRAGMDP,
                query=query,
                reason=reason,
                request_url=EUDRAGMDP_NON_COMPLIANCE_URL,
                request_method="POST",
                request_parameters=request_parameters,
                response=response.body,
                http_status=response.status,
                retrieved_at=retrieved_at,
            )
        page_bodies.append(response.body)
        rows, page_next, page_total = _eudra_page(response.body)
        if page_total is not None:
            total = page_total
        for cells in rows:
            all_records.append(_eudra_record(cells))
        if page_next is not None:
            page_match = re.search(r"param=(-?\d+)", page_next)
            if page_match is None:
                raise _SchemaChanged("EudraGMDP pagination link changed")
            if int(page_match.group(1)) < page_number:
                page_next = None
        next_url = (
            _validated_eudra_url(page_next, pagination=True) if page_next else None
        )
        if total is not None and len(all_records) >= total:
            next_url = None

    if total is not None and len(all_records) != total:
        raise _SchemaChanged("EudraGMDP pagination did not return the declared total")
    record_ids = [record.record_id for record in all_records]
    if len(record_ids) != len(set(record_ids)):
        raise _SchemaChanged("EudraGMDP returned duplicate record ids")
    matches = [record for record in all_records if _eudra_matches(record, query)]
    source_updated_at = max(
        (record.source_updated_at for record in matches if record.source_updated_at),
        default=None,
    )
    response_hash_input = b"".join(
        len(page).to_bytes(8, "big") + page for page in page_bodies
    )
    state = (
        RegulatoryScreeningState.HIT if matches else RegulatoryScreeningState.NO_HIT
    )
    return _make_result(
        source=RegulatoryScreeningSource.EUDRAGMDP,
        state=state,
        query=query,
        records=matches,
        request_url=EUDRAGMDP_NON_COMPLIANCE_URL,
        request_method="POST",
        request_parameters=request_parameters,
        response=response_hash_input,
        http_status=200,
        retrieved_at=retrieved_at,
        source_updated_at=source_updated_at,
        response_truncated=False,
    )


def screen_eudragmdp(
    *,
    manufacturer_name: str | None = None,
    country: str | None = None,
    city: str | None = None,
    oms_location_id: str | None = None,
) -> RegulatoryScreeningResult:
    """Screen a public manufacturer/site against EudraGMDP NCR records."""
    try:
        public_query = _safe_query(
            {
                "manufacturer_name": manufacturer_name,
                "country": country,
                "city": city,
                "oms_location_id": oms_location_id,
            }
        )
    except RegulatoryScreeningClientError:
        return _unavailable(
            source=RegulatoryScreeningSource.EUDRAGMDP,
            query=PublicScreeningQuery(),
            reason=ScreeningUnavailableReason.INVALID_PUBLIC_QUERY,
            request_url=EUDRAGMDP_NON_COMPLIANCE_URL,
            request_method="POST",
            request_parameters={},
        )
    try:
        query = EudraGmdpQuery.model_validate(public_query.model_dump())
    except ValidationError:
        reason = (
            ScreeningUnavailableReason.MISSING_PUBLIC_IDENTITY
            if not public_query.manufacturer_name or not public_query.country
            else ScreeningUnavailableReason.INVALID_PUBLIC_QUERY
        )
        return _unavailable(
            source=RegulatoryScreeningSource.EUDRAGMDP,
            query=public_query,
            reason=reason,
            request_url=EUDRAGMDP_NON_COMPLIANCE_URL,
            request_method="POST",
            request_parameters={},
        )

    request_parameters = _eudra_request_parameters(_now().date())
    try:
        return _cached_or_fetch(
            source=RegulatoryScreeningSource.EUDRAGMDP,
            query=query,
            request_parameters=request_parameters,
            fetch=lambda: _screen_eudragmdp_live(
                query, request_parameters=request_parameters
            ),
        )
    except _ResponseTooLarge:
        return _unavailable(
            source=RegulatoryScreeningSource.EUDRAGMDP,
            query=query,
            reason=ScreeningUnavailableReason.UPSTREAM_RESPONSE_TOO_LARGE,
            request_url=EUDRAGMDP_NON_COMPLIANCE_URL,
            request_method="POST",
            request_parameters=request_parameters,
        )
    except _SchemaChanged:
        return _unavailable(
            source=RegulatoryScreeningSource.EUDRAGMDP,
            query=query,
            reason=ScreeningUnavailableReason.UPSTREAM_SCHEMA_CHANGED,
            request_url=EUDRAGMDP_NON_COMPLIANCE_URL,
            request_method="POST",
            request_parameters=request_parameters,
        )
    except RegulatoryScreeningClientError:
        return _unavailable(
            source=RegulatoryScreeningSource.EUDRAGMDP,
            query=query,
            reason=ScreeningUnavailableReason.UPSTREAM_TRANSPORT_ERROR,
            request_url=EUDRAGMDP_NON_COMPLIANCE_URL,
            request_method="POST",
            request_parameters=request_parameters,
        )


def _lucene_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _openfda_term(field: str, value: str) -> str:
    return f'{field}:"{_lucene_escape(value)}"'


def _openfda_search(query: OpenFdaEnforcementQuery) -> str:
    parts: list[str] = []
    if query.manufacturer_name:
        firm = _openfda_term("recalling_firm", query.manufacturer_name)
        manufacturer = _openfda_term(
            "openfda.manufacturer_name", query.manufacturer_name
        )
        parts.append(f"({firm} OR {manufacturer})")
    if query.product_name:
        product = _openfda_term("product_description", query.product_name)
        brand = _openfda_term("openfda.brand_name", query.product_name)
        generic = _openfda_term("openfda.generic_name", query.product_name)
        parts.append(f"({product} OR {brand} OR {generic})")
    if query.active_ingredient:
        parts.append(_openfda_term("openfda.generic_name", query.active_ingredient))
    if query.defect_signature:
        parts.append(_openfda_term("reason_for_recall", query.defect_signature))
    return " AND ".join(parts)


def openfda_request_parameters(query: OpenFdaEnforcementQuery) -> dict[str, str]:
    """Return the exact public request parameters for an openFDA query."""
    return {"search": _openfda_search(query), "limit": str(MAX_OPENFDA_RECORDS)}


def _openfda_value(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _SchemaChanged(f"openFDA field {key} changed type")
    return value.strip() or None


def _openfda_harmonized_value(
    record: dict[str, Any], key: str
) -> str | None:
    harmonized = record.get("openfda")
    if harmonized is None:
        return None
    if not isinstance(harmonized, dict):
        raise _SchemaChanged("openFDA harmonized field changed type")
    value = harmonized.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _SchemaChanged(f"openFDA harmonized field {key} changed type")
    return next((item.strip() for item in value if item.strip()), None)


def _openfda_record(record: object) -> RegulatoryScreeningRecord:
    if not isinstance(record, dict):
        raise _SchemaChanged("openFDA result is not an object")
    record_id = next(
        (
            _openfda_value(record, key)
            for key in ("recall_number", "event_id", "report_number")
            if _openfda_value(record, key)
        ),
        None,
    )
    if not record_id:
        raise _SchemaChanged("openFDA result has no stable record id")
    source_updated_at = _date_value(
        _openfda_value(record, "report_date")
        or _openfda_value(record, "recall_initiation_date")
        or ""
    )
    product_name = _openfda_value(record, "product_description")
    if product_name is None:
        product_name = _openfda_harmonized_value(record, "brand_name")
    if product_name is None:
        product_name = _openfda_harmonized_value(record, "generic_name")
    return RegulatoryScreeningRecord(
        record_id=record_id,
        source_url=OPENFDA_DRUG_ENFORCEMENT_URL,
        organization_name=_openfda_value(record, "recalling_firm"),
        country=_openfda_value(record, "country"),
        city=_openfda_value(record, "city"),
        product_name=product_name,
        defect_signature=_openfda_value(record, "reason_for_recall"),
        source_updated_at=source_updated_at,
    )


def _openfda_error_is_no_hit(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    message = error.get("message")
    return code == "NOT_FOUND" and isinstance(message, str) and "no matches" in message.casefold()


def _screen_openfda_live(
    query: OpenFdaEnforcementQuery,
    *,
    request_url: str,
    request_parameters: dict[str, str],
) -> RegulatoryScreeningResult:
    retrieved_at = _now()
    request = urllib.request.Request(
        request_url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
    )
    opener = urllib.request.build_opener(_OpenFdaRedirectHandler())
    response = _fetch(request, opener=opener)
    _validated_openfda_url(
        response.url,
        expected_query=urllib.parse.urlsplit(request_url).query,
    )
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _SchemaChanged("openFDA response was not valid JSON") from exc

    if response.status == 404:
        if not _openfda_error_is_no_hit(payload):
            raise _SchemaChanged("openFDA 404 response changed")
        return _make_result(
            source=RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
            state=RegulatoryScreeningState.NO_HIT,
            query=query,
            records=[],
            request_url=request_url,
            request_method="GET",
            request_parameters=request_parameters,
            response=response.body,
            http_status=response.status,
            retrieved_at=retrieved_at,
        )
    if response.status == 429:
        return _unavailable(
            source=RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
            query=query,
            reason=ScreeningUnavailableReason.UPSTREAM_RATE_LIMITED,
            request_url=request_url,
            request_method="GET",
            request_parameters=request_parameters,
            response=response.body,
            http_status=response.status,
            retrieved_at=retrieved_at,
        )
    if response.status < 200 or response.status >= 300:
        return _unavailable(
            source=RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
            query=query,
            reason=ScreeningUnavailableReason.UPSTREAM_HTTP_ERROR,
            request_url=request_url,
            request_method="GET",
            request_parameters=request_parameters,
            response=response.body,
            http_status=response.status,
            retrieved_at=retrieved_at,
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise _SchemaChanged("openFDA results array is missing")
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise _SchemaChanged("openFDA metadata object is missing")
    records = [_openfda_record(item) for item in payload["results"]]
    if len(records) > MAX_OPENFDA_RECORDS:
        raise _SchemaChanged("openFDA returned more records than requested")
    meta_results = meta.get("results")
    if not isinstance(meta_results, dict):
        raise _SchemaChanged("openFDA metadata results object is missing")
    raw_total = meta_results.get("total")
    if not isinstance(raw_total, int) or raw_total < 0:
        raise _SchemaChanged("openFDA result total is missing or invalid")
    total = raw_total
    if total < len(records):
        raise _SchemaChanged("openFDA result total is inconsistent")
    raw_last_updated = meta.get("last_updated")
    source_updated_at = (
        _date_value(raw_last_updated) if isinstance(raw_last_updated, str) else None
    )
    if raw_last_updated is not None and not isinstance(raw_last_updated, str):
        raise _SchemaChanged("openFDA last_updated changed type")
    return _make_result(
        source=RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
        state=(
            RegulatoryScreeningState.HIT
            if records
            else RegulatoryScreeningState.NO_HIT
        ),
        query=query,
        records=records,
        request_url=request_url,
        request_method="GET",
        request_parameters=request_parameters,
        response=response.body,
        http_status=response.status,
        retrieved_at=retrieved_at,
        source_updated_at=source_updated_at
        or max(
            (record.source_updated_at for record in records if record.source_updated_at),
            default=None,
        ),
        response_truncated=total is not None and total > len(records),
    )


def screen_openfda_enforcement(
    *,
    manufacturer_name: str | None = None,
    product_name: str | None = None,
    active_ingredient: str | None = None,
    defect_signature: str | None = None,
) -> RegulatoryScreeningResult:
    """Screen public product/defect terms against openFDA drug enforcement."""
    try:
        public_query = _safe_query(
            {
                "manufacturer_name": manufacturer_name,
                "product_name": product_name,
                "active_ingredient": active_ingredient,
                "defect_signature": defect_signature,
            }
        )
    except RegulatoryScreeningClientError:
        return _unavailable(
            source=RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
            query=PublicScreeningQuery(),
            reason=ScreeningUnavailableReason.INVALID_PUBLIC_QUERY,
            request_url=OPENFDA_DRUG_ENFORCEMENT_URL,
            request_method="GET",
            request_parameters={},
        )
    try:
        query = OpenFdaEnforcementQuery.model_validate(public_query.model_dump())
    except ValidationError:
        reason = (
            ScreeningUnavailableReason.MISSING_PUBLIC_IDENTITY
            if not any(
                (
                    public_query.manufacturer_name,
                    public_query.product_name,
                    public_query.active_ingredient,
                    public_query.defect_signature,
                )
            )
            else ScreeningUnavailableReason.INVALID_PUBLIC_QUERY
        )
        return _unavailable(
            source=RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
            query=public_query,
            reason=reason,
            request_url=OPENFDA_DRUG_ENFORCEMENT_URL,
            request_method="GET",
            request_parameters={},
        )

    request_parameters = openfda_request_parameters(query)
    request_url = f"{OPENFDA_DRUG_ENFORCEMENT_URL}?{urllib.parse.urlencode(request_parameters)}"
    try:
        return _cached_or_fetch(
            source=RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
            query=query,
            request_parameters=request_parameters,
            fetch=lambda: _screen_openfda_live(
                query,
                request_url=request_url,
                request_parameters=request_parameters,
            ),
        )
    except _ResponseTooLarge:
        return _unavailable(
            source=RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
            query=query,
            reason=ScreeningUnavailableReason.UPSTREAM_RESPONSE_TOO_LARGE,
            request_url=request_url,
            request_method="GET",
            request_parameters=request_parameters,
        )
    except _SchemaChanged:
        return _unavailable(
            source=RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
            query=query,
            reason=ScreeningUnavailableReason.UPSTREAM_SCHEMA_CHANGED,
            request_url=request_url,
            request_method="GET",
            request_parameters=request_parameters,
        )
    except RegulatoryScreeningClientError:
        return _unavailable(
            source=RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
            query=query,
            reason=ScreeningUnavailableReason.UPSTREAM_TRANSPORT_ERROR,
            request_url=request_url,
            request_method="GET",
            request_parameters=request_parameters,
        )


def screening_advisory_findings(
    results: Iterable[RegulatoryScreeningResult],
) -> list[ScreeningAdvisoryFinding]:
    """Create deterministic, non-gating findings from captured results."""
    findings: list[ScreeningAdvisoryFinding] = []
    for result in results:
        source_label = (
            "EudraGMDP" if result.source is RegulatoryScreeningSource.EUDRAGMDP else "openFDA"
        )
        record_ids = [record.record_id for record in result.records]
        finding_key = "|".join(
            (
                result.source.value,
                result.state.value,
                result.unavailable_reason.value
                if result.unavailable_reason is not None
                else "",
                result.query.model_dump_json(),
                result.provenance.response_sha256 or "",
                ",".join(record_ids),
            )
        )
        finding_id = hashlib.sha256(finding_key.encode("utf-8")).hexdigest()
        if result.state is RegulatoryScreeningState.HIT:
            description = (
                f"{source_label} returned {len(record_ids)} matching public "
                f"record(s): {', '.join(record_ids)}. This is advisory context "
                "only and does not disqualify the supplier or establish root cause."
            )
            title = f"{source_label} public record match"
        elif result.state is RegulatoryScreeningState.NO_HIT:
            description = (
                f"{source_label} returned no matching public records for the "
                "submitted public query. No-hit is not supplier clearance and "
                "does not establish absence of risk."
            )
            title = f"{source_label} public search returned no hit"
        else:
            description = (
                f"{source_label} screening was unavailable "
                f"({result.unavailable_reason.value if result.unavailable_reason else 'unknown'}). "
                "No conclusion was drawn and no fallback data was used."
            )
            title = f"{source_label} screening unavailable"
        findings.append(
            ScreeningAdvisoryFinding(
                source=result.source,
                state=result.state,
                title=title,
                description=description,
                finding_id=finding_id,
                record_ids=record_ids,
                source_url=result.provenance.source_url,
                retrieved_at=result.provenance.retrieved_at,
                cache_status=result.provenance.cache_status,
            )
        )
    return findings
