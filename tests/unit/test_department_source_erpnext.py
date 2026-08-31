from __future__ import annotations

import datetime
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.branches import (
    _make_eudragmdp_screening_tool,
    _make_openfda_screening_tool,
    _make_supply_tool,
    _screening_query,
)
from app.domain.department_checks import check_coa
from app.domain.department_source import (
    DepartmentRecordMissing,
    coa_backend_name,
    get_coa_source,
    get_sibling_batch_source,
    reset_department_source_cache,
    sibling_batch_backend_name,
)
from app.domain.department_source_erpnext import (
    ERPNextApiError,
    ErpNextSupplySource,
    erpnext_credentials_for_tenant,
    reset_erpnext_credentials_cache,
)
from app.domain.regulatory_screening import unavailable_screening_result
from app.domain.regulatory_screening_models import (
    RegulatoryScreeningSource,
    ScreeningUnavailableReason,
)


@pytest.fixture(autouse=True)
def _reset_credentials() -> None:
    reset_department_source_cache()
    yield
    reset_department_source_cache()


def _credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "COUNTERSIGN_ERPNEXT_CREDENTIALS_JSON",
        json.dumps(
            {
                "ACME01": {
                    "base_url": "https://erp.example.test",
                    "api_key": "integration-key",
                    "api_secret": "integration-secret",
                }
            }
        ),
    )


def _coa_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "countersign.erpnext.supply.v1",
        "tenant_id": "ACME01",
        "batch_id": "BATCH-2026-001",
        "record_id": "CSR-0001",
        "source_uri": "https://erp.example.test/app/countersign-coa-verification/CSR-0001",
        "record_modified": "2026-08-29T12:00:00Z",
        "supplier_id": "SUP-441",
        "material_lot": "RM-LOT-88",
        "dispensed_lot": "RM-LOT-88",
        "coa_verified": True,
        "coa_verified_by": "quality.manager@example.test",
        "retest_date": "2027-01-15",
        "gmp_certificate_expiry": "2027-12-31",
        "quality_inspection_id": "MAT-QA-2026-0001",
        "quality_inspection_status": "Accepted",
        "quality_inspection_docstatus": 1,
        "attachment_sha256": "a" * 64,
        "attachment_is_private": True,
        "gmp_certificate_sha256": "c" * 64,
        "manufacturer_name": "Example Pharma Materials Ltd",
        "manufacturer_country": "Belgium",
        "manufacturer_city": "Puurs",
        "oms_location_id": "ORG-100020",
        "public_product_name": "Sterile excipient",
        "public_active_ingredient": "Example ingredient",
        "public_defect_signature": None,
    }
    payload.update(overrides)
    return {"message": payload}


def test_credentials_are_tenant_scoped_and_require_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    credentials = erpnext_credentials_for_tenant("ACME01")
    assert credentials.base_url == "https://erp.example.test"
    assert credentials.api_key == "integration-key"

    with pytest.raises(ERPNextApiError, match="No ERPNext credentials"):
        erpnext_credentials_for_tenant("OTHER")

    reset_erpnext_credentials_cache()
    monkeypatch.setenv(
        "COUNTERSIGN_ERPNEXT_CREDENTIALS_JSON",
        json.dumps(
            {
                "ACME01": {
                    "base_url": "http://erp.example.test",
                    "api_key": "key",
                    "api_secret": "secret",
                }
            }
        ),
    )
    with pytest.raises(ERPNextApiError, match="HTTPS"):
        erpnext_credentials_for_tenant("ACME01")


def test_coa_snapshot_is_strictly_mapped_with_real_attachment_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    requests: list[tuple[str, str]] = []

    def request_json(*, tenant_id: str, method: str, params: dict[str, str]) -> Any:
        requests.append((tenant_id, method))
        assert params == {"batch_id": "BATCH-2026-001"}
        return _coa_payload()

    source = ErpNextSupplySource(request_json=request_json)
    snapshot = source.coa(tenant_id="ACME01", batch_id="BATCH-2026-001")

    assert requests == [("ACME01", "get_coa_snapshot")]
    assert snapshot.system == "erpnext"
    assert snapshot.signed is True
    assert snapshot.gmp_certificate_current is True
    assert snapshot.material_lot == snapshot.dispensed_lot
    assert snapshot.public_active_ingredient == "Example ingredient"
    assert snapshot.attachment_sha256 == "a" * 64
    assert check_coa(snapshot) == []


def test_maintenance_binding_is_tenant_locked_and_uses_work_order_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)

    def request_json(*, tenant_id: str, method: str, params: dict[str, str]) -> Any:
        assert tenant_id == "ACME01"
        assert method == "get_maintenance_asset_binding"
        assert params == {"batch_id": "BATCH-2026-001"}
        return {
            "message": {
                "schema_version": "countersign.erpnext.supply.v1",
                "tenant_id": "ACME01",
                "batch_id": "BATCH-2026-001",
                "asset_code": "M-12",
                "work_order_id": "MFG-WO-2026-00001",
                "source_uri": "https://erp.example.test/app/work-order/MFG-WO-2026-00001",
                "batch_window_started_at": "2026-08-30T09:00:00Z",
                "batch_window_ended_at": "2026-08-30T16:00:00Z",
            }
        }

    binding = ErpNextSupplySource(request_json=request_json).maintenance_asset_binding(
        tenant_id="ACME01", batch_id="BATCH-2026-001"
    )

    assert binding.asset_code == "M-12"
    assert binding.work_order_id == "MFG-WO-2026-00001"


def test_unsigned_and_expired_erpnext_coa_reuses_existing_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)

    def request_json(**_: Any) -> Any:
        return _coa_payload(
            coa_verified=False,
            coa_verified_by=None,
            retest_date="2020-01-01",
            gmp_certificate_expiry="2020-01-01",
        )

    snapshot = ErpNextSupplySource(request_json=request_json).coa(
        tenant_id="ACME01", batch_id="BATCH-2026-001"
    )
    fields = {finding.field_name for finding in check_coa(snapshot)}
    assert fields == {"coa_signature", "retest_date", "gmp_certificate"}


def test_rejected_quality_inspection_is_a_cited_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    snapshot = ErpNextSupplySource(
        request_json=lambda **_: _coa_payload(quality_inspection_status="Rejected")
    ).coa(tenant_id="ACME01", batch_id="BATCH-2026-001")
    findings = check_coa(snapshot)
    inspection = next(
        finding for finding in findings if finding.field_name == "quality_inspection"
    )
    assert inspection.anchor is not None
    assert inspection.anchor.artifact_sha256 == "a" * 64


def test_coa_response_scope_and_provenance_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)

    for payload, match in [
        (_coa_payload(tenant_id="OTHER"), "scope"),
        (_coa_payload(attachment_is_private=False), "private"),
        (_coa_payload(attachment_sha256="not-a-sha256"), "schema"),
        (
            _coa_payload(source_uri="https://attacker.example/app/record/CSR-0001"),
            "host",
        ),
        (
            _coa_payload(
                source_uri="https://erp.example.test/app/countersign-coa-verification/OTHER"
            ),
            "record",
        ),
    ]:
        source = ErpNextSupplySource(
            request_json=lambda payload=payload, **_: payload
        )
        with pytest.raises((ERPNextApiError, DepartmentRecordMissing), match=match):
            source.coa(tenant_id="ACME01", batch_id="BATCH-2026-001")


def test_sibling_query_returns_only_same_lot_other_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)

    def request_json(**_: Any) -> Any:
        return {
            "message": {
                "schema_version": "countersign.erpnext.supply.v1",
                "tenant_id": "ACME01",
                "batch_id": "BATCH-2026-001",
                "material_lot": "RM-LOT-88",
                "source_uri": "https://erp.example.test/app/countersign-coa-verification/CSR-0001",
                "siblings": [
                    {
                        "batch_id": "BATCH-2026-002",
                        "material_lot": "RM-LOT-88",
                        "source_uri": "https://erp.example.test/app/stock-entry/STE-002",
                    },
                    {
                        "batch_id": "BATCH-2026-003",
                        "material_lot": "RM-LOT-88",
                        "source_uri": "https://erp.example.test/app/stock-entry/STE-003",
                    },
                ],
            }
        }

    lot, siblings = ErpNextSupplySource(
        request_json=request_json
    ).batches_sharing_material_lot(
        tenant_id="ACME01", batch_id="BATCH-2026-001"
    )
    assert lot == "RM-LOT-88"
    assert siblings == [
        (
            "BATCH-2026-002",
            "RM-LOT-88",
            "https://erp.example.test/app/stock-entry/STE-002",
        ),
        (
            "BATCH-2026-003",
            "RM-LOT-88",
            "https://erp.example.test/app/stock-entry/STE-003",
        ),
    ]


def test_sibling_response_rejects_duplicates_source_batch_and_wrong_lot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    base = {
        "schema_version": "countersign.erpnext.supply.v1",
        "tenant_id": "ACME01",
        "batch_id": "BATCH-2026-001",
        "material_lot": "RM-LOT-88",
        "source_uri": "https://erp.example.test/app/countersign-coa-verification/CSR-0001",
    }
    invalid_siblings = [
        [
            {
                "batch_id": "BATCH-2026-001",
                "material_lot": "RM-LOT-88",
                "source_uri": "https://erp.example.test/app/countersign-coa-verification/CSR-0001",
            }
        ],
        [
            {
                "batch_id": "BATCH-2026-002",
                "material_lot": "OTHER-LOT",
                "source_uri": "https://erp.example.test/app/countersign-coa-verification/CSR-0002",
            }
        ],
    ]
    for siblings in invalid_siblings:
        source = ErpNextSupplySource(
            request_json=lambda siblings=siblings, **_: {
                "message": {**base, "siblings": siblings}
            }
        )
        with pytest.raises(ERPNextApiError, match="sibling"):
            source.batches_sharing_material_lot(
                tenant_id="ACME01", batch_id="BATCH-2026-001"
            )


def test_sibling_response_rejects_unauthorized_source_uri_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    base = {
        "schema_version": "countersign.erpnext.supply.v1",
        "tenant_id": "ACME01",
        "batch_id": "BATCH-2026-001",
        "material_lot": "RM-LOT-88",
        "source_uri": "https://erp.example.test/app/countersign-coa-verification/CSR-0001",
    }
    source = ErpNextSupplySource(
        request_json=lambda **_: {
            "message": {
                **base,
                "siblings": [
                    {
                        "batch_id": "BATCH-2026-002",
                        "material_lot": "RM-LOT-88",
                        "source_uri": "https://erp.example.test/app/some-other-page/STE-2",
                    }
                ],
            }
        }
    )
    with pytest.raises(ERPNextApiError, match="path"):
        source.batches_sharing_material_lot(
            tenant_id="ACME01", batch_id="BATCH-2026-001"
        )


@pytest.mark.asyncio
async def test_supply_agent_tool_never_receives_coa_body_or_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    snapshot = ErpNextSupplySource(
        request_json=lambda **_: _coa_payload()
    ).coa(tenant_id="ACME01", batch_id="BATCH-2026-001")

    async def call_tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return snapshot.model_dump(mode="json")

    monkeypatch.setattr("app.domain.branches.call_department_tool", call_tool)
    bundle = SimpleNamespace(
        tenant_id="ACME01",
        batch_id="BATCH-2026-001",
        lifecycle_state="classified",
    )
    captured: dict[str, Any] = {}
    result = await _make_supply_tool(bundle, captured)()

    assert result == {
        "source_uri": snapshot.source_uri,
        "supplier_id": "SUP-441",
        "material_lot": "RM-LOT-88",
    }
    serialized = json.dumps(result)
    assert snapshot.attachment_sha256 not in serialized
    assert "Example ingredient" not in serialized


@pytest.mark.asyncio
async def test_duplicate_supplier_coa_call_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    snapshot = ErpNextSupplySource(
        request_json=lambda **_: _coa_payload()
    ).coa(tenant_id="ACME01", batch_id="BATCH-2026-001")
    calls = 0

    async def call_tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return snapshot.model_dump(mode="json")

    monkeypatch.setattr("app.domain.branches.call_department_tool", call_tool)
    bundle = SimpleNamespace(
        tenant_id="ACME01",
        batch_id="BATCH-2026-001",
        lifecycle_state="classified",
    )
    captured: dict[str, Any] = {}
    tool = _make_supply_tool(bundle, captured)

    first = await tool()
    second = await tool()

    assert calls == 1
    assert first == second


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "source", "capture_key"),
    [
        (
            _make_eudragmdp_screening_tool,
            RegulatoryScreeningSource.EUDRAGMDP,
            "eudragmdp",
        ),
        (
            _make_openfda_screening_tool,
            RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
            "openfda",
        ),
    ],
)
async def test_duplicate_regulatory_screening_call_is_cached(
    monkeypatch: pytest.MonkeyPatch,
    factory: Any,
    source: RegulatoryScreeningSource,
    capture_key: str,
) -> None:
    """A model retry must not issue two external regulator requests."""
    _credentials(monkeypatch)
    coa = ErpNextSupplySource(
        request_json=lambda **_: _coa_payload()
    ).coa(tenant_id="ACME01", batch_id="BATCH-2026-001")
    calls = 0

    async def screen(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        query, _ = _screening_query(coa, source)
        return unavailable_screening_result(
            source,
            query=query,
            reason=ScreeningUnavailableReason.MCP_UNAVAILABLE,
        )

    monkeypatch.setattr("app.domain.branches._run_screening", screen)
    captured: dict[str, Any] = {"coa": coa}
    tool = factory(captured)

    first = await tool()
    second = await tool()

    assert calls == 1
    assert first == second
    assert capture_key in captured


def test_regulatory_queries_never_include_internal_erpnext_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    snapshot = ErpNextSupplySource(
        request_json=lambda **_: _coa_payload()
    ).coa(tenant_id="ACME01", batch_id="BATCH-2026-001")

    for source in (
        RegulatoryScreeningSource.EUDRAGMDP,
        RegulatoryScreeningSource.OPENFDA_ENFORCEMENT,
    ):
        query, reason = _screening_query(snapshot, source)
        assert reason is None
        serialized = json.dumps(query.model_dump(exclude_none=True))
        assert "ACME01" not in serialized
        assert "BATCH-2026-001" not in serialized
        assert "SUP-441" not in serialized
        assert "RM-LOT-88" not in serialized


def test_backend_switches_report_erpnext(monkeypatch: pytest.MonkeyPatch) -> None:
    _credentials(monkeypatch)
    monkeypatch.setenv("COUNTERSIGN_COA_BACKEND", "erpnext")
    monkeypatch.setenv("COUNTERSIGN_SIBLING_BATCH_BACKEND", "erpnext")
    reset_department_source_cache()

    assert isinstance(get_coa_source(), ErpNextSupplySource)
    assert isinstance(get_sibling_batch_source(), ErpNextSupplySource)
    assert coa_backend_name() == "erpnext"
    assert sibling_batch_backend_name() == "erpnext"


def test_unknown_coa_backend_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COUNTERSIGN_COA_BACKEND", "typo")
    reset_department_source_cache()
    with pytest.raises(RuntimeError, match="Unsupported"):
        get_coa_source()


def test_gmp_certificate_is_computed_from_expiry_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)

    def request_json(**_: Any) -> Any:
        return _coa_payload(gmp_certificate_expiry="2026-08-29")

    source = ErpNextSupplySource(
        request_json=request_json,
        today=lambda: datetime.date(2026, 8, 29),
    )
    assert source.coa(
        tenant_id="ACME01", batch_id="BATCH-2026-001"
    ).gmp_certificate_current


def test_gmp_certificate_requires_recorded_attachment_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _credentials(monkeypatch)
    source = ErpNextSupplySource(
        request_json=lambda **_: _coa_payload(gmp_certificate_sha256=None)
    )
    assert not source.coa(
        tenant_id="ACME01", batch_id="BATCH-2026-001"
    ).gmp_certificate_current


@pytest.mark.parametrize(
    "base_url",
    [
        "http://erp.example.test",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://metadata.google.internal",
        "https://erp.local",
        "https://localhost",
        "https://erp.example.test:80",
        "https://erp.example.test:443/path",
        "https://user@erp.example.test",
        "https://erp.example.test?x=1",
    ],
)
def test_credentials_reject_unsafe_base_urls(
    base_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_erpnext_credentials_cache()
    monkeypatch.setenv(
        "COUNTERSIGN_ERPNEXT_CREDENTIALS_JSON",
        json.dumps(
            {
                "ACME01": {
                    "base_url": base_url,
                    "api_key": "key",
                    "api_secret": "secret",
                }
            }
        ),
    )
    with pytest.raises(ERPNextApiError):
        erpnext_credentials_for_tenant("ACME01")


def test_base_url_default_https_port_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_erpnext_credentials_cache()
    monkeypatch.setenv(
        "COUNTERSIGN_ERPNEXT_CREDENTIALS_JSON",
        json.dumps(
            {
                "ACME01": {
                    "base_url": "https://erp.example.test:443",
                    "api_key": "key",
                    "api_secret": "secret",
                }
            }
        ),
    )
    assert (
        erpnext_credentials_for_tenant("ACME01").base_url
        == "https://erp.example.test"
    )
