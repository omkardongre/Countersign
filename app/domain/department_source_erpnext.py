from __future__ import annotations

import datetime
import functools
import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.capa_models import (
    NonConformanceWriteRequest,
    NonConformanceWriteResult,
    QualityActionWriteRequest,
    QualityActionWriteResult,
)
from app.domain.department_models import CoaSnapshot
from app.domain.department_source import BACKEND_ERPNEXT, DepartmentRecordMissing

ERPNEXT_CREDENTIALS_ENV = "COUNTERSIGN_ERPNEXT_CREDENTIALS_JSON"
ERPNEXT_CREDENTIALS_PATH_ENV = "COUNTERSIGN_ERPNEXT_CREDENTIALS_PATH"
ERPNEXT_TIMEOUT_ENV = "COUNTERSIGN_ERPNEXT_TIMEOUT_SECONDS"
ERPNEXT_SCHEMA_VERSION = "countersign.erpnext.supply.v1"
_DEFAULT_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 512 * 1024
_METHODS = frozenset({"get_coa_snapshot", "get_batches_sharing_material_lot"})
_WRITE_METHODS = frozenset({"create_non_conformance", "create_quality_action"})
_API_PREFIX = "/api/method/countersign_erpnext.api.v1."
_TENANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_HOSTNAME_LENGTH = 253
_MAX_LABEL_LENGTH = 63
_METADATA_HOSTS = frozenset({
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google",
    "metadata.google.internal",
    "metadata.aws",
    "metadata.aws.internal",
    "169.254.169.254",
    "metadata.azure",
    "metadata.azure.internal",
})
_RESERVED_TLDS = frozenset({
    "local",
    "localhost",
    "internal",
    "intranet",
    "private",
    "corp",
    "home",
    "lan",
})


class ERPNextApiError(Exception):
    pass


@dataclass(frozen=True)
class ERPNextCredentials:
    base_url: str
    api_key: str
    api_secret: str


class _CredentialPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(min_length=1, max_length=512)
    api_secret: str = Field(min_length=1, max_length=1024)

    @field_validator("api_key", "api_secret")
    @classmethod
    def _valid_token_part(cls, value: str) -> str:
        if any(character in value for character in (":", "\r", "\n")):
            raise ValueError("ERPNext token components contain forbidden characters")
        try:
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("ERPNext token components must be ASCII") from exc
        return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _CoaApiRecord(_StrictModel):
    schema_version: Literal[ERPNEXT_SCHEMA_VERSION]
    tenant_id: str = Field(min_length=1, max_length=128)
    batch_id: str = Field(min_length=1, max_length=256)
    record_id: str = Field(min_length=1, max_length=256)
    source_uri: str = Field(min_length=1, max_length=4096)
    record_modified: datetime.datetime
    supplier_id: str = Field(min_length=1, max_length=256)
    material_lot: str = Field(min_length=1, max_length=256)
    dispensed_lot: str = Field(min_length=1, max_length=256)
    coa_verified: bool
    coa_verified_by: str | None = Field(default=None, max_length=256)
    retest_date: datetime.date
    gmp_certificate_expiry: datetime.date
    gmp_certificate_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    quality_inspection_id: str = Field(min_length=1, max_length=256)
    quality_inspection_status: str = Field(min_length=1, max_length=64)
    quality_inspection_docstatus: int = Field(ge=0, le=2)
    attachment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attachment_is_private: bool
    manufacturer_name: str | None = Field(default=None, max_length=512)
    manufacturer_country: str | None = Field(default=None, max_length=128)
    manufacturer_city: str | None = Field(default=None, max_length=256)
    oms_location_id: str | None = Field(default=None, max_length=256)
    public_product_name: str | None = Field(default=None, max_length=512)
    public_active_ingredient: str | None = Field(default=None, max_length=512)
    public_defect_signature: str | None = Field(default=None, max_length=512)


class _SiblingApiRecord(_StrictModel):
    batch_id: str = Field(min_length=1, max_length=256)
    material_lot: str = Field(min_length=1, max_length=256)
    source_uri: str = Field(min_length=1, max_length=4096)


class _SiblingApiResponse(_StrictModel):
    schema_version: Literal[ERPNEXT_SCHEMA_VERSION]
    tenant_id: str = Field(min_length=1, max_length=128)
    batch_id: str = Field(min_length=1, max_length=256)
    material_lot: str = Field(min_length=1, max_length=256)
    source_uri: str = Field(min_length=1, max_length=4096)
    siblings: list[_SiblingApiRecord] = Field(max_length=1000)


class _CoaEnvelope(_StrictModel):
    message: _CoaApiRecord


class _SiblingEnvelope(_StrictModel):
    message: _SiblingApiResponse


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise ERPNextApiError("ERPNext API redirects are not permitted.")


def _valid_hostname(hostname: str | None) -> None:
    if not hostname:
        raise ERPNextApiError("ERPNext URL hostname is missing.")
    hostname = hostname.lower().rstrip(".")
    if hostname in _METADATA_HOSTS:
        raise ERPNextApiError("ERPNext URL hostname is not allowed.")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ERPNextApiError("ERPNext URL must not be an IP literal.")
    if len(hostname) > _MAX_HOSTNAME_LENGTH:
        raise ERPNextApiError("ERPNext URL hostname is too long.")
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9][A-Za-z0-9-]*)+",
        hostname,
    ):
        raise ERPNextApiError("ERPNext URL hostname is not a valid FQDN.")
    for label in hostname.split("."):
        if (
            not label
            or len(label) > _MAX_LABEL_LENGTH
            or label.startswith("-")
            or label.endswith("-")
        ):
            raise ERPNextApiError("ERPNext URL hostname label is invalid.")
    tld = hostname.split(".")[-1]
    if tld in _RESERVED_TLDS or re.fullmatch(r"\d+", tld):
        raise ERPNextApiError("ERPNext URL TLD is not allowed.")


def _port_or_default(parsed: urllib.parse.SplitResult) -> int:
    try:
        return parsed.port or 443
    except ValueError as exc:
        raise ERPNextApiError("ERPNext URL port is invalid.") from exc


def _normalized_netloc(parsed: urllib.parse.SplitResult) -> str:
    if not parsed.hostname:
        raise ERPNextApiError("ERPNext URL hostname is missing.")
    _valid_hostname(parsed.hostname)
    hostname = parsed.hostname.lower().rstrip(".")
    port = _port_or_default(parsed)
    if port == 443:
        return hostname
    return f"{hostname}:{port}"


def _validated_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() != "https":
        raise ERPNextApiError("ERPNext base URLs must use HTTPS.")
    try:
        port = _port_or_default(parsed)
    except ERPNextApiError:
        raise
    if port != 443:
        raise ERPNextApiError("ERPNext base URL must use the default HTTPS port (443).")
    if parsed.username or parsed.password:
        raise ERPNextApiError("ERPNext base URL authority is invalid.")
    if parsed.query or parsed.fragment:
        raise ERPNextApiError("ERPNext base URLs may not contain query or fragment data.")
    if parsed.path not in ("", "/"):
        raise ERPNextApiError("ERPNext base URLs may not contain a path.")
    return urllib.parse.urlunsplit(
        ("https", _normalized_netloc(parsed), "", "", "")
    )


@functools.cache
def _erpnext_credentials() -> dict[str, ERPNextCredentials]:
    raw: str | None = None
    path = os.environ.get(ERPNEXT_CREDENTIALS_PATH_ENV, "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            raise ERPNextApiError(
                f"{ERPNEXT_CREDENTIALS_PATH_ENV} cannot be read."
            ) from exc
    if raw is None:
        raw = os.environ.get(ERPNEXT_CREDENTIALS_ENV, "")
    if not raw:
        raise ERPNextApiError(
            f"ERPNext credentials are not configured: set {ERPNEXT_CREDENTIALS_ENV} "
            f"or {ERPNEXT_CREDENTIALS_PATH_ENV}."
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ERPNextApiError("ERPNext credentials are not valid JSON.") from exc
    if not isinstance(payload, dict) or not payload or len(payload) > 100:
        raise ERPNextApiError(
            "ERPNext credentials must map 1-100 tenant ids to credentials."
        )
    credentials: dict[str, ERPNextCredentials] = {}
    for tenant_id, value in payload.items():
        if not isinstance(tenant_id, str) or not _TENANT_ID.fullmatch(tenant_id):
            raise ERPNextApiError("ERPNext credential tenant id is invalid.")
        try:
            parsed = _CredentialPayload.model_validate(value)
        except ValidationError as exc:
            raise ERPNextApiError(
                f"ERPNext credential schema is invalid for tenant {tenant_id!r}."
            ) from exc
        credentials[tenant_id] = ERPNextCredentials(
            base_url=_validated_base_url(parsed.base_url),
            api_key=parsed.api_key,
            api_secret=parsed.api_secret,
        )
    return credentials


def erpnext_credentials_for_tenant(tenant_id: str) -> ERPNextCredentials:
    credentials = _erpnext_credentials().get(tenant_id)
    if credentials is None:
        raise ERPNextApiError(
            f"No ERPNext credentials are configured for tenant {tenant_id!r}."
        )
    return credentials


def reset_erpnext_credentials_cache() -> None:
    _erpnext_credentials.cache_clear()


def _timeout_seconds() -> float:
    raw = os.environ.get(ERPNEXT_TIMEOUT_ENV, "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise ERPNextApiError(f"{ERPNEXT_TIMEOUT_ENV} must be numeric.") from exc
    if not 1.0 <= value <= 30.0:
        raise ERPNextApiError(f"{ERPNEXT_TIMEOUT_ENV} must be between 1 and 30.")
    return value


def _request_json(
    *, tenant_id: str, method: str, params: dict[str, str]
) -> dict[str, Any]:
    if method not in _METHODS:
        raise ERPNextApiError("ERPNext API method is not allow-listed.")
    credentials = erpnext_credentials_for_tenant(tenant_id)
    query = urllib.parse.urlencode(params)
    url = f"{credentials.base_url}{_API_PREFIX}{method}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": (
                f"token {credentials.api_key}:{credentials.api_secret}"
            ),
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_RejectRedirects())
    try:
        with opener.open(request, timeout=_timeout_seconds()) as response:
            content_type = response.headers.get_content_type()
            if content_type != "application/json":
                raise ERPNextApiError("ERPNext API returned a non-JSON response.")
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except ERPNextApiError:
        raise
    except urllib.error.HTTPError as exc:
        raise ERPNextApiError(
            f"ERPNext API {method} failed with HTTP {exc.code}."
        ) from exc
    except urllib.error.URLError as exc:
        raise ERPNextApiError(f"ERPNext API {method} transport failed.") from exc
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ERPNextApiError("ERPNext API response exceeded the size limit.")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ERPNextApiError("ERPNext API returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise ERPNextApiError("ERPNext API response schema is invalid.")
    return payload


def _request_json_post(
    *, tenant_id: str, method: str, body: dict[str, Any]
) -> dict[str, Any]:
    """POST a JSON body to a whitelisted ERPNext custom API endpoint.

    Same security posture as ``_request_json``: HTTPS-only, no redirects,
    token auth, bounded response, JSON content-type check. The method must
    be in ``_WRITE_METHODS``.
    """
    if method not in _WRITE_METHODS:
        raise ERPNextApiError("ERPNext write API method is not allow-listed.")
    credentials = erpnext_credentials_for_tenant(tenant_id)
    url = f"{credentials.base_url}{_API_PREFIX}{method}"
    encoded = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": (
                f"token {credentials.api_key}:{credentials.api_secret}"
            ),
        },
        method="POST",
    )
    opener = urllib.request.build_opener(_RejectRedirects())
    try:
        with opener.open(request, timeout=_timeout_seconds()) as response:
            content_type = response.headers.get_content_type()
            if content_type != "application/json":
                raise ERPNextApiError("ERPNext API returned a non-JSON response.")
            response_body = response.read(_MAX_RESPONSE_BYTES + 1)
    except ERPNextApiError:
        raise
    except urllib.error.HTTPError as exc:
        raise ERPNextApiError(
            f"ERPNext API {method} failed with HTTP {exc.code}."
        ) from exc
    except urllib.error.URLError as exc:
        raise ERPNextApiError(f"ERPNext API {method} transport failed.") from exc
    if len(response_body) > _MAX_RESPONSE_BYTES:
        raise ERPNextApiError("ERPNext API response exceeded the size limit.")
    try:
        payload = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ERPNextApiError("ERPNext API returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise ERPNextApiError("ERPNext API response schema is invalid.")
    return payload


class _NonConformanceApiResponse(_StrictModel):
    schema_version: Literal[ERPNEXT_SCHEMA_VERSION]
    tenant_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=256)
    non_conformance_id: str = Field(min_length=1, max_length=256)
    non_conformance_url: str = Field(min_length=1, max_length=4096)
    was_existing: bool


class _QualityActionApiResponse(_StrictModel):
    schema_version: Literal[ERPNEXT_SCHEMA_VERSION]
    tenant_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=256)
    quality_action_id: str = Field(min_length=1, max_length=256)
    quality_action_url: str = Field(min_length=1, max_length=4096)
    was_existing: bool


class _NonConformanceEnvelope(_StrictModel):
    message: _NonConformanceApiResponse


class _QualityActionEnvelope(_StrictModel):
    message: _QualityActionApiResponse


_NC_URI_PREFIX = "/app/non-conformance/"
_QA_URI_PREFIX = "/app/quality-action/"


def _validate_write_uri(
    source_uri: str, base_url: str, *, prefix: str, record_id: str
) -> None:
    """Same URI validation as ``_validate_source_uri`` but for write-back
    record URLs (Non Conformance / Quality Action)."""
    source = urllib.parse.urlsplit(source_uri)
    base = urllib.parse.urlsplit(base_url)
    if source.scheme != "https":
        raise ERPNextApiError("ERPNext write-back URI must use HTTPS.")
    try:
        source_port = _port_or_default(source)
        base_port = _port_or_default(base)
    except ERPNextApiError:
        raise
    if (source.hostname or "").lower() != (base.hostname or "").lower():
        raise ERPNextApiError("ERPNext write-back URI host does not match its tenant site.")
    if source_port != base_port:
        raise ERPNextApiError("ERPNext write-back URI port does not match its tenant site.")
    if source.username or source.password or source.query or source.fragment:
        raise ERPNextApiError("ERPNext write-back URI schema is invalid.")
    if not source.path.startswith(prefix):
        raise ERPNextApiError("ERPNext write-back URI path is not allowed.")
    expected_path = prefix + urllib.parse.quote(record_id, safe="")
    if source.path != expected_path:
        raise ERPNextApiError("ERPNext write-back URI record does not match the response.")


_COA_URI_PREFIX = "/app/countersign-coa-verification/"
_STOCK_ENTRY_URI_PREFIX = "/app/stock-entry/"
_SOURCE_URI_PREFIXES = (_COA_URI_PREFIX, _STOCK_ENTRY_URI_PREFIX)


def _validate_source_uri(
    source_uri: str,
    base_url: str,
    *,
    record_id: str | None = None,
    allowed_prefixes: tuple[str, ...] = (_COA_URI_PREFIX,),
) -> None:
    source = urllib.parse.urlsplit(source_uri)
    base = urllib.parse.urlsplit(base_url)
    if source.scheme != "https":
        raise ERPNextApiError("ERPNext source URI must use HTTPS.")
    try:
        source_port = _port_or_default(source)
        base_port = _port_or_default(base)
    except ERPNextApiError:
        raise
    if (source.hostname or "").lower() != (base.hostname or "").lower():
        raise ERPNextApiError("ERPNext source URI host does not match its tenant site.")
    if source_port != base_port:
        raise ERPNextApiError("ERPNext source URI port does not match its tenant site.")
    if (
        source.username
        or source.password
        or source.query
        or source.fragment
    ):
        raise ERPNextApiError("ERPNext source URI schema is invalid.")
    if not any(source.path.startswith(prefix) for prefix in allowed_prefixes):
        raise ERPNextApiError("ERPNext source URI path is not allowed.")
    if record_id is not None:
        expected_path = _COA_URI_PREFIX + urllib.parse.quote(record_id, safe="")
        if source.path != expected_path:
            raise ERPNextApiError("ERPNext source URI record does not match the response.")


RequestJson = Callable[..., dict[str, Any]]


class ErpNextSupplySource:
    backend_name = BACKEND_ERPNEXT
    backend_name_sibling_batch = BACKEND_ERPNEXT

    def __init__(
        self,
        *,
        request_json: RequestJson = _request_json,
        today: Callable[[], datetime.date] | None = None,
    ) -> None:
        self._request_json = request_json
        self._today = today or (lambda: datetime.datetime.now(datetime.UTC).date())

    def coa(self, *, tenant_id: str, batch_id: str) -> CoaSnapshot:
        try:
            envelope = _CoaEnvelope.model_validate(
                self._request_json(
                    tenant_id=tenant_id,
                    method="get_coa_snapshot",
                    params={"batch_id": batch_id},
                )
            )
        except ValidationError as exc:
            raise ERPNextApiError("ERPNext CoA response schema is invalid.") from exc
        record = envelope.message
        if record.tenant_id != tenant_id or record.batch_id != batch_id:
            raise ERPNextApiError("ERPNext CoA response scope does not match the request.")
        credentials = erpnext_credentials_for_tenant(tenant_id)
        _validate_source_uri(
            record.source_uri, credentials.base_url, record_id=record.record_id
        )
        if not record.attachment_is_private:
            raise DepartmentRecordMissing("ERPNext CoA attachment is not private.")
        if record.quality_inspection_docstatus == 2:
            raise DepartmentRecordMissing("ERPNext Quality Inspection is cancelled.")
        signed = bool(
            record.coa_verified
            and record.coa_verified_by
            and record.quality_inspection_docstatus == 1
        )
        return CoaSnapshot(
            system=BACKEND_ERPNEXT,
            source_uri=record.source_uri,
            supplier_id=record.supplier_id,
            material_lot=record.material_lot,
            dispensed_lot=record.dispensed_lot,
            signed=signed,
            retest_date=record.retest_date,
            gmp_certificate_current=(
                record.gmp_certificate_expiry >= self._today()
                and record.gmp_certificate_sha256 is not None
            ),
            quality_inspection_accepted=(
                record.quality_inspection_status == "Accepted"
                and record.quality_inspection_docstatus == 1
            ),
            record_id=record.record_id,
            record_modified=record.record_modified,
            quality_inspection_id=record.quality_inspection_id,
            attachment_sha256=record.attachment_sha256,
            manufacturer_name=record.manufacturer_name,
            manufacturer_country=record.manufacturer_country,
            manufacturer_city=record.manufacturer_city,
            oms_location_id=record.oms_location_id,
            public_product_name=record.public_product_name,
            public_active_ingredient=record.public_active_ingredient,
            public_defect_signature=record.public_defect_signature,
        )

    def batches_sharing_material_lot(
        self, *, tenant_id: str, batch_id: str
    ) -> tuple[str, list[tuple[str, str, str]]]:
        try:
            envelope = _SiblingEnvelope.model_validate(
                self._request_json(
                    tenant_id=tenant_id,
                    method="get_batches_sharing_material_lot",
                    params={"batch_id": batch_id},
                )
            )
        except ValidationError as exc:
            raise ERPNextApiError("ERPNext sibling response schema is invalid.") from exc
        response = envelope.message
        if response.tenant_id != tenant_id or response.batch_id != batch_id:
            raise ERPNextApiError("ERPNext sibling response scope does not match the request.")
        credentials = erpnext_credentials_for_tenant(tenant_id)
        _validate_source_uri(response.source_uri, credentials.base_url)
        seen: set[str] = set()
        siblings: list[tuple[str, str, str]] = []
        for sibling in response.siblings:
            if (
                sibling.batch_id == batch_id
                or sibling.batch_id in seen
                or sibling.material_lot != response.material_lot
            ):
                raise ERPNextApiError("ERPNext sibling result violates the lot contract.")
            _validate_source_uri(
                sibling.source_uri,
                credentials.base_url,
                allowed_prefixes=_SOURCE_URI_PREFIXES,
            )
            seen.add(sibling.batch_id)
            siblings.append(
                (sibling.batch_id, sibling.material_lot, sibling.source_uri)
            )
        return response.material_lot, siblings

    def create_non_conformance(
        self, *, request: NonConformanceWriteRequest
    ) -> NonConformanceWriteResult:
        """Create (or re-use) a Non Conformance in ERPNext.

        Idempotent by ``(tenant_id, case_id)``: the ERPNext endpoint checks
        for an existing NC with those custom fields before creating. A
        resume-triggered rerun re-uses the same record rather than
        duplicating it.
        """
        try:
            envelope = _NonConformanceEnvelope.model_validate(
                _request_json_post(
                    tenant_id=request.tenant_id,
                    method="create_non_conformance",
                    body=request.model_dump(mode="json"),
                )
            )
        except ValidationError as exc:
            raise ERPNextApiError(
                "ERPNext Non Conformance response schema is invalid."
            ) from exc
        record = envelope.message
        if record.tenant_id != request.tenant_id or record.case_id != request.case_id:
            raise ERPNextApiError(
                "ERPNext Non Conformance response scope does not match the request."
            )
        credentials = erpnext_credentials_for_tenant(request.tenant_id)
        _validate_write_uri(
            record.non_conformance_url,
            credentials.base_url,
            prefix=_NC_URI_PREFIX,
            record_id=record.non_conformance_id,
        )
        return NonConformanceWriteResult(
            non_conformance_id=record.non_conformance_id,
            non_conformance_url=record.non_conformance_url,
            was_existing=record.was_existing,
        )

    def create_quality_action(
        self, *, request: QualityActionWriteRequest
    ) -> QualityActionWriteResult:
        """Create (or re-use) a Quality Action in ERPNext linked to an NC.

        Idempotent by ``(tenant_id, case_id, action_type)``: the ERPNext
        endpoint checks for an existing QA with those custom fields before
        creating.
        """
        try:
            envelope = _QualityActionEnvelope.model_validate(
                _request_json_post(
                    tenant_id=request.tenant_id,
                    method="create_quality_action",
                    body=request.model_dump(mode="json"),
                )
            )
        except ValidationError as exc:
            raise ERPNextApiError(
                "ERPNext Quality Action response schema is invalid."
            ) from exc
        record = envelope.message
        if record.tenant_id != request.tenant_id or record.case_id != request.case_id:
            raise ERPNextApiError(
                "ERPNext Quality Action response scope does not match the request."
            )
        credentials = erpnext_credentials_for_tenant(request.tenant_id)
        _validate_write_uri(
            record.quality_action_url,
            credentials.base_url,
            prefix=_QA_URI_PREFIX,
            record_id=record.quality_action_id,
        )
        return QualityActionWriteResult(
            quality_action_id=record.quality_action_id,
            quality_action_url=record.quality_action_url,
            was_existing=record.was_existing,
        )
