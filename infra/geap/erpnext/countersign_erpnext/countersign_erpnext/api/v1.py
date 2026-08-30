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

import re
import urllib.parse

import frappe
from frappe import _
from frappe.utils import get_url

from countersign_erpnext import SCHEMA_VERSION

API_ROLE = "Countersign ERPNext API Reader"
_SOURCE_SELECT = """
    SELECT
        access.name AS access_id,
        access.tenant_id,
        access.company,
        coa.tenant_id AS coa_tenant_id,
        coa.name AS record_id,
        coa.modified AS record_modified,
        coa.docstatus AS coa_docstatus,
        coa.verification_status,
        coa.supplier_coa_verified,
        coa.reviewed_by,
        coa.reviewed_on,
        coa.verified_by,
        coa.retest_date,
        coa.gmp_certificate_expiry,
        coa.gmp_certificate_sha256,
        coa.coa_content_hash,
        coa.coa_sha256,
        coa.material_lot,
        coa.public_manufacturer_name,
        coa.public_manufacturer_country,
        coa.public_manufacturer_city,
        coa.public_oms_location_id,
        coa.public_product_name,
        coa.active_ingredient,
        coa.public_defect_signature,
        supplier.name AS supplier_id,
        supplier.disabled AS supplier_disabled,
        receipt.name AS purchase_receipt_id,
        receipt.docstatus AS purchase_receipt_docstatus,
        receipt.company AS purchase_receipt_company,
        receipt.supplier AS purchase_receipt_supplier,
        (
            SELECT COUNT(DISTINCT receipt_item.name)
            FROM `tabPurchase Receipt Item` receipt_item
            LEFT JOIN `tabSerial and Batch Entry` receipt_batch
              ON receipt_batch.parent = receipt_item.serial_and_batch_bundle
             AND receipt_batch.batch_no = coa.material_lot
            WHERE receipt_item.parent = coa.purchase_receipt
              AND receipt_item.item_code = coa.raw_material_item
              AND (receipt_item.batch_no = coa.material_lot OR receipt_batch.name IS NOT NULL)
        ) AS purchase_receipt_lot_count,
        raw_item.name AS raw_material_item_id,
        raw_item.disabled AS raw_material_item_disabled,
        raw_batch.name AS material_lot_id,
        raw_batch.disabled AS material_lot_disabled,
        raw_batch.item AS material_lot_item,
        inspection.name AS quality_inspection_id,
        inspection.docstatus AS quality_inspection_docstatus,
        inspection.status AS quality_inspection_status,
        inspection.reference_type AS quality_inspection_reference_type,
        inspection.reference_name AS quality_inspection_reference_name,
        inspection.company AS quality_inspection_company,
        inspection.item_code AS quality_inspection_item,
        inspection.batch_no AS quality_inspection_batch,
        attachment.name AS attachment_file_id,
        attachment.is_private AS attachment_is_private,
        attachment.content_hash AS attachment_content_hash,
        attachment.attached_to_doctype AS attachment_doctype,
        attachment.attached_to_name AS attachment_document,
        source_genealogy.name AS source_genealogy_id,
        source_genealogy.manufacture_stock_entry AS source_stock_entry_id,
        finished_batch.name AS finished_batch_id,
        finished_batch.disabled AS finished_batch_disabled,
        finished_batch.item AS finished_batch_item,
        finished_item.name AS finished_item_id,
        finished_item.disabled AS finished_item_disabled,
        manufacture.docstatus AS manufacture_docstatus,
        manufacture.purpose AS manufacture_purpose,
        manufacture.company AS manufacture_company,
        (
            SELECT MIN(COALESCE(NULLIF(source_detail.batch_no, ''), source_batch.batch_no))
            FROM `tabStock Entry Detail` source_detail
            LEFT JOIN `tabSerial and Batch Entry` source_batch
              ON source_batch.parent = source_detail.serial_and_batch_bundle
            WHERE source_detail.parent = source_genealogy.manufacture_stock_entry
              AND source_detail.item_code = coa.raw_material_item
              AND source_detail.is_finished_item = 0
              AND source_detail.transfer_qty > 0
              AND source_detail.s_warehouse IS NOT NULL
              AND source_detail.s_warehouse <> ''
              AND COALESCE(
                  NULLIF(source_detail.batch_no, ''), source_batch.batch_no
              ) = coa.material_lot
        ) AS dispensed_lot,
        (
            SELECT COUNT(DISTINCT finished_detail.name)
            FROM `tabStock Entry Detail` finished_detail
            LEFT JOIN `tabSerial and Batch Entry` finished_bundle_batch
              ON finished_bundle_batch.parent = finished_detail.serial_and_batch_bundle
             AND finished_bundle_batch.batch_no = source_genealogy.finished_batch
            WHERE finished_detail.parent = source_genealogy.manufacture_stock_entry
              AND finished_detail.item_code = source_genealogy.finished_item
              AND finished_detail.is_finished_item = 1
              AND finished_detail.transfer_qty > 0
              AND finished_detail.t_warehouse IS NOT NULL
              AND finished_detail.t_warehouse <> ''
              AND (
                  finished_detail.batch_no = source_genealogy.finished_batch
                  OR finished_bundle_batch.name IS NOT NULL
              )
        ) AS finished_output_count
    FROM `tabCountersign Sponsor Tenant Access` access
    JOIN `tabCountersign CoA Verification` coa
      ON coa.company = access.company
     AND coa.tenant_id = access.tenant_id
     AND coa.docstatus != 2
    JOIN `tabCountersign Finished Batch Genealogy` source_genealogy
      ON source_genealogy.parent = coa.name
     AND source_genealogy.parenttype = 'Countersign CoA Verification'
     AND source_genealogy.parentfield = 'finished_batches'
     AND source_genealogy.finished_batch = %(batch_id)s
    LEFT JOIN `tabSupplier` supplier ON supplier.name = coa.supplier
    LEFT JOIN `tabPurchase Receipt` receipt ON receipt.name = coa.purchase_receipt
    LEFT JOIN `tabItem` raw_item ON raw_item.name = coa.raw_material_item
    LEFT JOIN `tabBatch` raw_batch ON raw_batch.name = coa.material_lot
    LEFT JOIN `tabQuality Inspection` inspection ON inspection.name = coa.quality_inspection
    LEFT JOIN `tabFile` attachment
      ON attachment.file_url = coa.coa_attachment
     AND attachment.attached_to_doctype = 'Countersign CoA Verification'
     AND attachment.attached_to_name = coa.name
     AND attachment.attached_to_field = 'coa_attachment'
    LEFT JOIN `tabBatch` finished_batch ON finished_batch.name = source_genealogy.finished_batch
    LEFT JOIN `tabItem` finished_item ON finished_item.name = source_genealogy.finished_item
    LEFT JOIN `tabStock Entry` manufacture
      ON manufacture.name = source_genealogy.manufacture_stock_entry
    WHERE access.user = %(user)s
      AND access.enabled = 1
"""
_SIBLING_SELECT = """
    SELECT
        genealogy.name AS genealogy_id,
        genealogy.finished_batch AS batch_id,
        genealogy.finished_item AS finished_item_id,
        genealogy.manufacture_stock_entry AS stock_entry_id,
        finished_batch.disabled AS batch_disabled,
        finished_batch.item AS batch_item,
        finished_item.disabled AS item_disabled,
        manufacture.docstatus AS manufacture_docstatus,
        manufacture.purpose AS manufacture_purpose,
        manufacture.company AS manufacture_company,
        EXISTS(
            SELECT 1
            FROM `tabStock Entry Detail` source_detail
            LEFT JOIN `tabSerial and Batch Entry` source_batch
              ON source_batch.parent = source_detail.serial_and_batch_bundle
             AND source_batch.batch_no = %(material_lot)s
            WHERE source_detail.parent = genealogy.manufacture_stock_entry
              AND source_detail.item_code = %(raw_material_item)s
              AND source_detail.is_finished_item = 0
              AND source_detail.transfer_qty > 0
              AND source_detail.s_warehouse IS NOT NULL
              AND source_detail.s_warehouse <> ''
              AND (source_detail.batch_no = %(material_lot)s OR source_batch.name IS NOT NULL)
        ) AS source_lot_consumed,
        EXISTS(
            SELECT 1
            FROM `tabStock Entry Detail` finished_detail
            LEFT JOIN `tabSerial and Batch Entry` finished_bundle_batch
              ON finished_bundle_batch.parent = finished_detail.serial_and_batch_bundle
             AND finished_bundle_batch.batch_no = genealogy.finished_batch
            WHERE finished_detail.parent = genealogy.manufacture_stock_entry
              AND finished_detail.item_code = genealogy.finished_item
              AND finished_detail.is_finished_item = 1
              AND finished_detail.transfer_qty > 0
              AND finished_detail.t_warehouse IS NOT NULL
              AND finished_detail.t_warehouse <> ''
              AND (
                  finished_detail.batch_no = genealogy.finished_batch
                  OR finished_bundle_batch.name IS NOT NULL
              )
        ) AS finished_batch_produced
    FROM `tabCountersign Finished Batch Genealogy` genealogy
    LEFT JOIN `tabBatch` finished_batch ON finished_batch.name = genealogy.finished_batch
    LEFT JOIN `tabItem` finished_item ON finished_item.name = genealogy.finished_item
    LEFT JOIN `tabStock Entry` manufacture ON manufacture.name = genealogy.manufacture_stock_entry
    WHERE genealogy.parent = %(record_id)s
      AND genealogy.parenttype = 'Countersign CoA Verification'
      AND genealogy.parentfield = 'finished_batches'
      AND genealogy.finished_batch != %(batch_id)s
    ORDER BY genealogy.idx
    LIMIT 1002
"""


def _deny():
    frappe.throw(_("The requested supply evidence is unavailable."), frappe.PermissionError)


def _require_api_caller():
    request = getattr(frappe.local, "request", None)
    if request is not None and request.method != "GET":
        _deny()
    authorization = frappe.get_request_header("Authorization", "")
    if not authorization.startswith("token "):
        _deny()
    user = frappe.session.user
    if not user or user == "Guest" or API_ROLE not in frappe.get_roles(user):
        _deny()
    user_record = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
    if not user_record or not user_record.enabled or user_record.user_type != "System User":
        _deny()
    return user


def _batch_id(value):
    value = str(value or "").strip()
    if not value or len(value) > 256:
        _deny()
    return value


def _fetch_source(user, batch_id):
    rows = frappe.db.sql(
        _SOURCE_SELECT + " ORDER BY coa.name, attachment.name LIMIT 3",
        {"user": user, "batch_id": batch_id},
        as_dict=True,
    )
    if len(rows) != 1:
        _deny()
    _validate_source(rows[0], batch_id)
    return rows[0]


def _validate_source(row, batch_id):
    required = (
        row.access_id,
        row.tenant_id,
        row.company,
        row.coa_tenant_id,
        row.record_id,
        row.record_modified,
        row.reviewed_by,
        row.reviewed_on,
        row.retest_date,
        row.gmp_certificate_expiry,
        row.gmp_certificate_sha256,
        row.coa_content_hash,
        row.coa_sha256,
        row.material_lot,
        row.supplier_id,
        row.purchase_receipt_id,
        row.raw_material_item_id,
        row.material_lot_id,
        row.quality_inspection_id,
        row.attachment_file_id,
        row.attachment_content_hash,
        row.source_genealogy_id,
        row.source_stock_entry_id,
        row.finished_batch_id,
        row.finished_item_id,
        row.dispensed_lot,
    )
    outcome_consistent = bool(row.supplier_coa_verified) == (
        row.verification_status == "Verified"
    )
    verified_identity_consistent = bool(row.verified_by) == bool(row.supplier_coa_verified)
    valid = (
        all(required)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", row.tenant_id)
        and row.coa_tenant_id == row.tenant_id
        and row.coa_docstatus == 1
        and row.verification_status in {"Verified", "Rejected"}
        and outcome_consistent
        and verified_identity_consistent
        and row.purchase_receipt_docstatus == 1
        and row.purchase_receipt_company == row.company
        and row.purchase_receipt_supplier == row.supplier_id
        and row.purchase_receipt_lot_count > 0
        and not row.supplier_disabled
        and not row.raw_material_item_disabled
        and not row.material_lot_disabled
        and row.material_lot_item == row.raw_material_item_id
        and row.quality_inspection_docstatus == 1
        and row.quality_inspection_status in {"Accepted", "Rejected"}
        and row.quality_inspection_reference_type == "Purchase Receipt"
        and row.quality_inspection_reference_name == row.purchase_receipt_id
        and row.quality_inspection_company == row.company
        and row.quality_inspection_item == row.raw_material_item_id
        and row.quality_inspection_batch == row.material_lot_id
        and row.attachment_is_private == 1
        and row.attachment_content_hash == row.coa_content_hash
        and re.fullmatch(r"[0-9a-f]{32}", row.coa_content_hash)
        and re.fullmatch(r"[0-9a-f]{64}", row.coa_sha256)
        and re.fullmatch(r"[0-9a-f]{64}", row.gmp_certificate_sha256)
        and row.attachment_doctype == "Countersign CoA Verification"
        and row.attachment_document == row.record_id
        and row.finished_batch_id == batch_id
        and not row.finished_batch_disabled
        and not row.finished_item_disabled
        and row.finished_batch_item == row.finished_item_id
        and row.manufacture_docstatus == 1
        and row.manufacture_purpose == "Manufacture"
        and row.manufacture_company == row.company
        and row.dispensed_lot == row.material_lot
        and row.finished_output_count > 0
    )
    if not valid:
        _deny()


def _source_uri(record_id):
    path = f"/app/countersign-coa-verification/{urllib.parse.quote(record_id, safe='')}"
    source_uri = get_url(path)
    parsed = urllib.parse.urlsplit(source_uri)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        _deny()
    return source_uri


def _stock_entry_uri(stock_entry_id):
    path = f"/app/stock-entry/{urllib.parse.quote(stock_entry_id, safe='')}"
    source_uri = get_url(path)
    parsed = urllib.parse.urlsplit(source_uri)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        _deny()
    return source_uri


def _nullable(value):
    value = str(value or "").strip()
    return value or None


def _coa_response(row, batch_id):
    return {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": row.tenant_id,
        "batch_id": batch_id,
        "record_id": row.record_id,
        "source_uri": _source_uri(row.record_id),
        "record_modified": row.record_modified,
        "supplier_id": row.supplier_id,
        "material_lot": row.material_lot,
        "dispensed_lot": row.dispensed_lot,
        "coa_verified": bool(row.supplier_coa_verified),
        "coa_verified_by": _nullable(row.verified_by),
        "retest_date": row.retest_date,
        "gmp_certificate_expiry": row.gmp_certificate_expiry,
        "gmp_certificate_sha256": row.gmp_certificate_sha256,
        "quality_inspection_id": row.quality_inspection_id,
        "quality_inspection_status": row.quality_inspection_status,
        "quality_inspection_docstatus": row.quality_inspection_docstatus,
        "attachment_sha256": row.coa_sha256,
        "attachment_is_private": bool(row.attachment_is_private),
        "manufacturer_name": _nullable(row.public_manufacturer_name),
        "manufacturer_country": _nullable(row.public_manufacturer_country),
        "manufacturer_city": _nullable(row.public_manufacturer_city),
        "oms_location_id": _nullable(row.public_oms_location_id),
        "public_product_name": _nullable(row.public_product_name),
        "public_active_ingredient": _nullable(row.active_ingredient),
        "public_defect_signature": _nullable(row.public_defect_signature),
    }


@frappe.whitelist(allow_guest=False, methods=["GET"])
def get_coa_snapshot(batch_id=None):
    user = _require_api_caller()
    batch_id = _batch_id(batch_id)
    return _coa_response(_fetch_source(user, batch_id), batch_id)


@frappe.whitelist(allow_guest=False, methods=["GET"])
def get_batches_sharing_material_lot(batch_id=None):
    user = _require_api_caller()
    batch_id = _batch_id(batch_id)
    source = _fetch_source(user, batch_id)
    rows = frappe.db.sql(
        _SIBLING_SELECT,
        {
            "batch_id": batch_id,
            "material_lot": source.material_lot,
            "raw_material_item": source.raw_material_item_id,
            "record_id": source.record_id,
        },
        as_dict=True,
    )
    if len(rows) > 1000:
        _deny()
    seen = set()
    for row in rows:
        valid = (
            row.genealogy_id
            and row.batch_id
            and row.finished_item_id
            and row.stock_entry_id
            and row.batch_id != batch_id
            and row.batch_id not in seen
            and not row.batch_disabled
            and not row.item_disabled
            and row.batch_item == row.finished_item_id
            and row.manufacture_docstatus == 1
            and row.manufacture_purpose == "Manufacture"
            and row.manufacture_company == source.company
            and row.source_lot_consumed
            and row.finished_batch_produced
        )
        if not valid:
            _deny()
        seen.add(row.batch_id)
    source_uri = _source_uri(source.record_id)
    return {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": source.tenant_id,
        "batch_id": batch_id,
        "material_lot": source.material_lot,
        "source_uri": source_uri,
        "siblings": [
            {
                "batch_id": row.batch_id,
                "material_lot": source.material_lot,
                "source_uri": _stock_entry_uri(row.stock_entry_id),
            }
            for row in rows
        ],
    }
