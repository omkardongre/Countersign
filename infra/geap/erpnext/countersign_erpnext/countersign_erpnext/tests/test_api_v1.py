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

import hashlib
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from countersign_erpnext import SCHEMA_VERSION
from countersign_erpnext.api import v1
from countersign_erpnext.countersign_erpnext.doctype.countersign_coa_verification import (
    CountersignCoAVerification,
)


class TestSupplyApiV1(FrappeTestCase):
    def _source_row(self):
        return frappe._dict(
            access_id="api@example.test",
            tenant_id="tenant-a",
            company="CDMO Company",
            coa_tenant_id="tenant-a",
            record_id="CS-COA-2026-00001",
            record_modified=datetime(2026, 1, 1, 12, 0, 0),
            coa_docstatus=1,
            verification_status="Verified",
            supplier_coa_verified=1,
            reviewed_by="quality@example.test",
            reviewed_on=datetime(2026, 1, 1, 11, 59, 0),
            verified_by="quality@example.test",
            retest_date=date(2020, 1, 1),
            gmp_certificate_expiry=date(2020, 1, 1),
            gmp_certificate_sha256="d" * 64,
            coa_content_hash="a" * 32,
            coa_sha256="b" * 64,
            material_lot="RAW-LOT-1",
            public_manufacturer_name="Public Manufacturer",
            public_manufacturer_country="United States",
            public_manufacturer_city="Public City",
            public_oms_location_id="OMS-1",
            public_product_name="Public Product",
            active_ingredient="Ingredient",
            public_defect_signature="Known Defect",
            supplier_id="SUP-1",
            supplier_disabled=0,
            purchase_receipt_id="MAT-PRE-2026-00001",
            purchase_receipt_docstatus=1,
            purchase_receipt_company="CDMO Company",
            purchase_receipt_supplier="SUP-1",
            purchase_receipt_lot_count=1,
            raw_material_item_id="RAW-ITEM-1",
            raw_material_item_disabled=0,
            material_lot_id="RAW-LOT-1",
            material_lot_disabled=0,
            material_lot_item="RAW-ITEM-1",
            quality_inspection_id="QI-2026-00001",
            quality_inspection_docstatus=1,
            quality_inspection_status="Accepted",
            quality_inspection_reference_type="Purchase Receipt",
            quality_inspection_reference_name="MAT-PRE-2026-00001",
            quality_inspection_company="CDMO Company",
            quality_inspection_item="RAW-ITEM-1",
            quality_inspection_batch="RAW-LOT-1",
            attachment_file_id="FILE-1",
            attachment_is_private=1,
            attachment_content_hash="a" * 32,
            attachment_doctype="Countersign CoA Verification",
            attachment_document="CS-COA-2026-00001",
            source_genealogy_id="GEN-1",
            source_stock_entry_id="STE-1",
            finished_batch_id="FINISHED-1",
            finished_batch_disabled=0,
            finished_batch_item="FINISHED-ITEM-1",
            finished_item_id="FINISHED-ITEM-1",
            finished_item_disabled=0,
            manufacture_docstatus=1,
            manufacture_purpose="Manufacture",
            manufacture_company="CDMO Company",
            dispensed_lot="RAW-LOT-1",
            finished_output_count=1,
        )

    def test_coa_response_matches_supply_contract_and_exposes_no_attachment_content(self):
        row = self._source_row()
        v1._validate_source(row, "FINISHED-1")
        with patch.object(
            v1, "get_url", side_effect=lambda path: f"https://erp.example.test{path}"
        ):
            response = v1._coa_response(row, "FINISHED-1")
        self.assertEqual(
            set(response),
            {
                "schema_version",
                "tenant_id",
                "batch_id",
                "record_id",
                "source_uri",
                "record_modified",
                "supplier_id",
                "material_lot",
                "dispensed_lot",
                "coa_verified",
                "coa_verified_by",
                "retest_date",
                "gmp_certificate_expiry",
                "gmp_certificate_sha256",
                "quality_inspection_id",
                "quality_inspection_status",
                "quality_inspection_docstatus",
                "attachment_sha256",
                "attachment_is_private",
                "manufacturer_name",
                "manufacturer_country",
                "manufacturer_city",
                "oms_location_id",
                "public_product_name",
                "public_active_ingredient",
                "public_defect_signature",
            },
        )
        self.assertEqual(response["schema_version"], "countersign.erpnext.supply.v1")
        self.assertEqual(response["tenant_id"], "tenant-a")
        self.assertEqual(
            response["source_uri"],
            "https://erp.example.test/app/countersign-coa-verification/CS-COA-2026-00001",
        )
        self.assertEqual(response["batch_id"], "FINISHED-1")
        self.assertEqual(response["dispensed_lot"], "RAW-LOT-1")
        self.assertEqual(response["attachment_sha256"], "b" * 64)
        self.assertEqual(response["gmp_certificate_sha256"], "d" * 64)
        serialized = frappe.as_json(response)
        self.assertNotIn("attachment_file_id", serialized)
        self.assertNotIn("attachment_content_hash", serialized)
        self.assertNotIn("file_url", serialized)
        self.assertNotIn("content_base64", serialized)
        self.assertNotIn("extracted_text", serialized)

    def test_adverse_submitted_evidence_is_returned(self):
        row = self._source_row()
        row.verification_status = "Rejected"
        row.supplier_coa_verified = 0
        row.verified_by = None
        row.quality_inspection_status = "Rejected"
        v1._validate_source(row, "FINISHED-1")
        with patch.object(v1, "get_url", return_value="https://erp.example.test/app/x"):
            response = v1._coa_response(row, "FINISHED-1")
        self.assertFalse(response["coa_verified"])
        self.assertIsNone(response["coa_verified_by"])
        self.assertEqual(response["quality_inspection_status"], "Rejected")
        self.assertEqual(response["retest_date"], date(2020, 1, 1))
        self.assertEqual(response["gmp_certificate_expiry"], date(2020, 1, 1))

    def test_cancelled_inconsistent_or_public_evidence_fails_closed(self):
        for field, value in (
            ("coa_docstatus", 2),
            ("purchase_receipt_docstatus", 0),
            ("quality_inspection_docstatus", 2),
            ("attachment_is_private", 0),
            ("attachment_content_hash", "c" * 32),
            ("coa_sha256", "not-sha256"),
            ("gmp_certificate_sha256", "not-sha256"),
            ("dispensed_lot", "OTHER-LOT"),
            ("manufacture_docstatus", 0),
            ("finished_output_count", 0),
        ):
            row = self._source_row()
            row[field] = value
            with self.assertRaises(frappe.PermissionError):
                v1._validate_source(row, "FINISHED-1")

    def test_lookup_is_by_finished_batch_and_tenant_is_server_bound(self):
        with patch.object(v1.frappe.db, "sql", return_value=[self._source_row()]) as sql:
            row = v1._fetch_source("api@example.test", "FINISHED-1")
        query, parameters = sql.call_args.args
        self.assertIn("source_genealogy.finished_batch = %(batch_id)s", query)
        self.assertIn("coa.company = access.company", query)
        self.assertIn("coa.tenant_id = access.tenant_id", query)
        self.assertIn("access.tenant_id", query)
        self.assertEqual(parameters, {"user": "api@example.test", "batch_id": "FINISHED-1"})
        self.assertEqual(row.tenant_id, "tenant-a")

    def test_same_company_different_tenant_is_rejected(self):
        row = self._source_row()
        row.coa_tenant_id = "tenant-b"
        with self.assertRaises(frappe.PermissionError):
            v1._validate_source(row, "FINISHED-1")

    def test_missing_or_ambiguous_finished_batch_fails_closed(self):
        for rows in ([], [self._source_row(), self._source_row()]):
            with patch.object(v1.frappe.db, "sql", return_value=rows):
                with self.assertRaises(frappe.PermissionError):
                    v1._fetch_source("api@example.test", "FINISHED-1")

    def test_sibling_response_excludes_source_and_uses_per_sibling_stock_entry_uri(self):
        sibling = frappe._dict(
            genealogy_id="GEN-2",
            batch_id="FINISHED-2",
            finished_item_id="FINISHED-ITEM-2",
            stock_entry_id="STE-2",
            batch_disabled=0,
            batch_item="FINISHED-ITEM-2",
            item_disabled=0,
            manufacture_docstatus=1,
            manufacture_purpose="Manufacture",
            manufacture_company="CDMO Company",
            source_lot_consumed=1,
            finished_batch_produced=1,
        )
        with (
            patch.object(v1, "_require_api_caller", return_value="api@example.test"),
            patch.object(v1.frappe.db, "sql", side_effect=[[self._source_row()], [sibling]]),
            patch.object(
                v1,
                "get_url",
                side_effect=lambda path: f"https://erp.example.test{path}",
            ),
        ):
            response = v1.get_batches_sharing_material_lot(batch_id="FINISHED-1")
        self.assertEqual(
            response,
            {
                "schema_version": SCHEMA_VERSION,
                "tenant_id": "tenant-a",
                "batch_id": "FINISHED-1",
                "material_lot": "RAW-LOT-1",
                "source_uri": "https://erp.example.test/app/countersign-coa-verification/CS-COA-2026-00001",
                "siblings": [
                    {
                        "batch_id": "FINISHED-2",
                        "material_lot": "RAW-LOT-1",
                        "source_uri": "https://erp.example.test/app/stock-entry/STE-2",
                    }
                ],
            },
        )

    def test_coa_uniqueness_is_scoped_by_company_tenant_and_material_lot(self):
        document = SimpleNamespace(
            company="CDMO Company",
            tenant_id="tenant-a",
            material_lot="RAW-LOT-1",
            name="CS-COA-2026-00001",
        )
        with patch.object(frappe.db, "exists", return_value=None) as exists:
            CountersignCoAVerification._validate_unique_evidence(document)
        self.assertEqual(
            exists.call_args.args[1],
            {
                "company": "CDMO Company",
                "tenant_id": "tenant-a",
                "material_lot": "RAW-LOT-1",
                "docstatus": ["!=", 2],
                "name": ["!=", "CS-COA-2026-00001"],
            },
        )

    def test_private_file_bytes_are_pinned_with_independent_sha256(self):
        content = b"exact private CoA bytes\x00\xff"
        document = SimpleNamespace(
            coa_attachment="/private/files/coa.pdf",
            coa_content_hash=None,
            coa_sha256=None,
            doctype="Countersign CoA Verification",
            name="CS-COA-2026-00001",
        )
        md5_hash = hashlib.md5(content, usedforsecurity=False).hexdigest()
        file_row = frappe._dict(name="FILE-1", is_private=1, content_hash=md5_hash)
        file_document = SimpleNamespace(get_content=lambda: content)
        with (
            patch.object(frappe, "get_all", return_value=[file_row]),
            patch.object(frappe, "get_doc", return_value=file_document),
        ):
            CountersignCoAVerification._validate_attachment(document)
        self.assertEqual(document.coa_content_hash, md5_hash)
        self.assertEqual(document.coa_sha256, hashlib.sha256(content).hexdigest())

    def test_batch_id_is_required_and_bounded(self):
        for value in (None, "", "x" * 257):
            with self.assertRaises(frappe.PermissionError):
                v1._batch_id(value)

    def test_token_authorization_header_is_required(self):
        for header_value in ("", "Basic abc", "Bearer xyz"):
            with (
                patch.object(v1.frappe, "get_request_header", return_value=header_value),
                patch.object(v1.frappe, "throw", side_effect=frappe.PermissionError),
            ):
                with self.assertRaises(frappe.PermissionError):
                    v1._require_api_caller()
