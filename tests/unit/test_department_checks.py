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

"""Tests for LIMS / EM / CoA deterministic department checks."""

import datetime

from app.domain.compliance_models import ComplianceFindingType
from app.domain.department_catalog import lookup_department_snapshot
from app.domain.department_checks import check_coa, check_em, check_lims
from app.domain.department_models import (
    CoaSnapshot,
    EmReadingRow,
    EmSnapshot,
    LimsResultRow,
    LimsSnapshot,
)


def test_catalog_has_synthetic_aurex_batch() -> None:
    snapshot = lookup_department_snapshot(tenant_id="aurex", batch_id="AX-2291")
    assert snapshot is not None
    assert snapshot.lims.system == "cloud_sql"
    assert snapshot.em.system == "bigquery"
    assert snapshot.coa.system == "drive"
    assert check_lims(snapshot.lims) == []
    assert check_em(snapshot.em) == []
    assert check_coa(snapshot.coa) == []


def test_lims_out_of_specification_is_cited() -> None:
    snapshot = LimsSnapshot(
        source_uri="sql://lims.aurex.qc_results?batch_id=AX-2291",
        rows=[
            LimsResultRow(
                test_code="ASSAY",
                label="Assay",
                result_value=80.0,
                unit="%LC",
                spec_min=95.0,
                spec_max=105.0,
                sample_id="QC-AX-2291-01",
            )
        ],
    )
    findings = check_lims(snapshot)
    assert len(findings) == 1
    assert findings[0].finding_type is ComplianceFindingType.OUT_OF_SPECIFICATION
    assert findings[0].anchor is not None
    assert findings[0].anchor.source_uri == snapshot.source_uri


def test_em_action_limit_is_cited() -> None:
    snapshot = EmSnapshot(
        source_uri="bq://em.aurex.viable_air?batch_id=AX-2291",
        rows=[
            EmReadingRow(
                location_id="FILL-R1",
                parameter="viable_air_cfu",
                label="Viable air count",
                value=12.0,
                unit="cfu/m3",
                alert_limit=5.0,
                action_limit=10.0,
                recorded_at=datetime.datetime(2026, 8, 1, 10, 0, tzinfo=datetime.UTC),
            )
        ],
    )
    findings = check_em(snapshot)
    assert len(findings) == 1
    assert "action limit" in findings[0].description


def test_coa_lot_mismatch_is_cited() -> None:
    snapshot = CoaSnapshot(
        source_uri="drive://synthetic/coa/mismatch.pdf",
        supplier_id="SUP-441",
        material_lot="LOT-99",
        dispensed_lot="LOT-88",
        signed=True,
        retest_date=datetime.date(2027, 1, 15),
        gmp_certificate_current=True,
    )
    findings = check_coa(snapshot)
    assert any(f.field_name == "material_lot" for f in findings)


def test_expired_coa_retest_is_cited() -> None:
    snapshot = CoaSnapshot(
        source_uri="drive://synthetic/coa/expired.pdf",
        supplier_id="SUP-441",
        material_lot="LOT-88",
        dispensed_lot="LOT-88",
        signed=True,
        retest_date=datetime.date(2020, 1, 1),
        gmp_certificate_current=True,
    )
    findings = check_coa(snapshot)
    assert any(f.field_name == "retest_date" for f in findings)
