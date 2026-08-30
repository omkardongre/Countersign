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
CAPA_WRITE_ROLE = "Countersign ERPNext CAPA Writer"
_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_DETAILS_LENGTH = 8000
_MAX_SUBJECT_LENGTH = 256
_MAX_BATCH_LENGTH = 256
_MAX_CASE_LENGTH = 256
_NC_URI_PREFIX = "/app/non-conformance/"
_QA_URI_PREFIX = "/app/quality-action/"
# The Quality Procedure that all Countersign-created NCs are filed against.
# Created by post_setup.sh during ERPNext initialization.
COUNTERSIGN_QUALITY_PROCEDURE = "Countersign Deviation Management"
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


# ---------------------------------------------------------------------------
# CAPA write-back endpoints (Non Conformance + Quality Action)
#
# These are the only POST endpoints on this custom API surface. They are
# authenticated by the same token auth as the read endpoints, but require a
# separate role (``CAPA_WRITE_ROLE``) so a read-only API user cannot create
# records. Idempotency is enforced by checking for an existing record with
# the same ``(tenant_id, case_id)`` (NC) or ``(tenant_id, case_id,
# corrective_preventive)`` (QA) custom fields before creating.
#
# Real ERPNext doctype field names are used throughout:
#   Non Conformance: subject (reqd), procedure (Link, reqd), status, details
#   Quality Action: corrective_preventive (reqd), resolutions (child table),
#                   status, date, procedure
# Custom fields (countersign_tenant_id, countersign_case_id,
# countersign_batch_id, countersign_non_conformance) are installed via
# fixtures in hooks.py and capa_custom_fields.json.
# ---------------------------------------------------------------------------


def _deny_write():
    frappe.throw(
        _("The CAPA write-back request is not permitted."),
        frappe.PermissionError,
    )


def _require_capa_writer():
    """Same token-auth check as ``_require_api_caller``, but for POST and
    the CAPA Writer role. Rejects GET requests and read-only API users.
    """
    request = getattr(frappe.local, "request", None)
    if request is None or request.method != "POST":
        _deny_write()
    authorization = frappe.get_request_header("Authorization", "")
    if not authorization.startswith("token "):
        _deny_write()
    user = frappe.session.user
    if not user or user == "Guest":
        _deny_write()
    if CAPA_WRITE_ROLE not in frappe.get_roles(user):
        _deny_write()
    user_record = frappe.db.get_value(
        "User", user, ["enabled", "user_type"], as_dict=True
    )
    if not user_record or not user_record.enabled or user_record.user_type != "System User":
        _deny_write()
    return user


def _parse_write_body():
    """Parse and validate the JSON request body for CAPA write endpoints."""
    import json as _json

    raw = frappe.request.get_data(as_text=True)
    if not raw:
        _deny_write()
    try:
        body = _json.loads(raw)
    except (ValueError, TypeError):
        _deny_write()
    if not isinstance(body, dict):
        _deny_write()
    return body


def _validate_capa_field(value, field_name, max_length, pattern=None):
    value = str(value or "").strip()
    if not value or len(value) > max_length:
        _deny_write()
    if pattern and not re.fullmatch(pattern, value):
        _deny_write()
    return value


def _resolve_tenant_company(user, tenant_id):
    """Resolve the ERPNext company for a tenant_id via the access table."""
    access = frappe.db.get_value(
        "Countersign Sponsor Tenant Access",
        {"user": user, "tenant_id": tenant_id, "enabled": 1},
        "company",
    )
    if not access:
        _deny_write()
    return access


def _ensure_quality_procedure():
    """Verify the Countersign Quality Procedure exists (created by
    post_setup.sh). The Non Conformance doctype requires a ``procedure``
    Link field — without this, NC creation fails."""
    if not frappe.db.exists("Quality Procedure", COUNTERSIGN_QUALITY_PROCEDURE):
        _deny_write()
    return COUNTERSIGN_QUALITY_PROCEDURE


def _nc_uri(record_id):
    path = f"{_NC_URI_PREFIX}{urllib.parse.quote(record_id, safe='')}"
    source_uri = get_url(path)
    parsed = urllib.parse.urlsplit(source_uri)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        _deny_write()
    return source_uri


def _qa_uri(record_id):
    path = f"{_QA_URI_PREFIX}{urllib.parse.quote(record_id, safe='')}"
    source_uri = get_url(path)
    parsed = urllib.parse.urlsplit(source_uri)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        _deny_write()
    return source_uri


def _find_existing_nc(tenant_id, case_id):
    """Find an existing Non Conformance with the same tenant/case custom
    fields. Returns the NC name or None."""
    return frappe.db.get_value(
        "Non Conformance",
        {
            "countersign_tenant_id": tenant_id,
            "countersign_case_id": case_id,
            "docstatus": ["!=", 2],
        },
        "name",
    )


def _find_existing_qa(tenant_id, case_id, corrective_preventive):
    """Find an existing Quality Action with the same tenant/case/type.

    Uses the real ``corrective_preventive`` field on the Quality Action
    doctype (not a custom field), plus the custom ``countersign_*`` fields
    for tenant/case idempotency.
    """
    return frappe.db.get_value(
        "Quality Action",
        {
            "countersign_tenant_id": tenant_id,
            "countersign_case_id": case_id,
            "corrective_preventive": corrective_preventive,
            "docstatus": ["!=", 2],
        },
        "name",
    )


@frappe.whitelist(allow_guest=False, methods=["POST"])
def create_non_conformance():
    """Create (or re-use) a Non Conformance in ERPNext for a deviation case.

    Maps the Countersign case to the real Non Conformance doctype fields:
      - ``subject``: short case summary (reqd by doctype)
      - ``procedure``: Link to the Countersign Quality Procedure (reqd by doctype)
      - ``details``: full NC description (Text Editor)
      - ``status``: "Open"
      - custom: ``countersign_tenant_id``, ``countersign_case_id``,
        ``countersign_batch_id``

    Idempotent by ``(tenant_id, case_id)``: if an NC with those custom
    fields already exists and is not cancelled, it is returned rather than
    duplicated.
    """
    user = _require_capa_writer()
    body = _parse_write_body()

    tenant_id = _validate_capa_field(
        body.get("tenant_id"), "tenant_id", 128, _TENANT_ID_RE.pattern
    )
    case_id = _validate_capa_field(
        body.get("case_id"), "case_id", _MAX_CASE_LENGTH
    )
    batch_id = _validate_capa_field(
        body.get("batch_id"), "batch_id", _MAX_BATCH_LENGTH
    )
    subject = _validate_capa_field(
        body.get("subject"), "subject", _MAX_SUBJECT_LENGTH
    )
    details = _validate_capa_field(
        body.get("details"), "details", _MAX_DETAILS_LENGTH
    )
    _resolve_tenant_company(user, tenant_id)
    procedure = _ensure_quality_procedure()

    # Idempotency: check for an existing NC with the same tenant/case.
    existing = _find_existing_nc(tenant_id, case_id)
    if existing:
        return {
            "schema_version": SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "case_id": case_id,
            "non_conformance_id": existing,
            "non_conformance_url": _nc_uri(existing),
            "was_existing": True,
        }

    nc = frappe.get_doc({
        "doctype": "Non Conformance",
        "subject": subject,
        "procedure": procedure,
        "status": "Open",
        "details": details,
        "countersign_tenant_id": tenant_id,
        "countersign_case_id": case_id,
        "countersign_batch_id": batch_id,
    })
    nc.insert(ignore_permissions=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "case_id": case_id,
        "non_conformance_id": nc.name,
        "non_conformance_url": _nc_uri(nc.name),
        "was_existing": False,
    }


@frappe.whitelist(allow_guest=False, methods=["POST"])
def create_quality_action():
    """Create (or re-use) a Quality Action in ERPNext linked to a Non
    Conformance.

    Maps the Countersign CAPA to the real Quality Action doctype fields:
      - ``corrective_preventive``: "Corrective" or "Preventive" (reqd by doctype)
      - ``resolutions``: child table with one row (problem + resolution text)
      - ``status``: "Open"
      - ``procedure``: Link to the Countersign Quality Procedure
      - custom: ``countersign_tenant_id``, ``countersign_case_id``,
        ``countersign_batch_id``, ``countersign_non_conformance``

    Idempotent by ``(tenant_id, case_id, corrective_preventive)``.
    """
    user = _require_capa_writer()
    body = _parse_write_body()

    tenant_id = _validate_capa_field(
        body.get("tenant_id"), "tenant_id", 128, _TENANT_ID_RE.pattern
    )
    case_id = _validate_capa_field(
        body.get("case_id"), "case_id", _MAX_CASE_LENGTH
    )
    batch_id = _validate_capa_field(
        body.get("batch_id"), "batch_id", _MAX_BATCH_LENGTH
    )
    non_conformance_id = _validate_capa_field(
        body.get("non_conformance_id"), "non_conformance_id", _MAX_CASE_LENGTH
    )
    corrective_preventive = str(body.get("action_type") or "").strip()
    if corrective_preventive not in ("Corrective", "Preventive"):
        _deny_write()
    problem = _validate_capa_field(
        body.get("problem"), "problem", _MAX_DETAILS_LENGTH
    )
    resolution_text = _validate_capa_field(
        body.get("resolution"), "resolution", _MAX_DETAILS_LENGTH
    )
    _resolve_tenant_company(user, tenant_id)
    procedure = _ensure_quality_procedure()

    # Verify the NC exists and belongs to this tenant.
    nc_tenant = frappe.db.get_value(
        "Non Conformance", non_conformance_id, "countersign_tenant_id"
    )
    if not nc_tenant or nc_tenant != tenant_id:
        _deny_write()

    # Idempotency: check for an existing QA with the same tenant/case/type.
    existing = _find_existing_qa(tenant_id, case_id, corrective_preventive)
    if existing:
        return {
            "schema_version": SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "case_id": case_id,
            "quality_action_id": existing,
            "quality_action_url": _qa_uri(existing),
            "was_existing": True,
        }

    qa = frappe.get_doc({
        "doctype": "Quality Action",
        "corrective_preventive": corrective_preventive,
        "status": "Open",
        "procedure": procedure,
        "countersign_tenant_id": tenant_id,
        "countersign_case_id": case_id,
        "countersign_batch_id": batch_id,
        "countersign_non_conformance": non_conformance_id,
        "resolutions": [
            {
                "problem": problem,
                "resolution": resolution_text,
                "status": "Open",
            }
        ],
    })
    qa.insert(ignore_permissions=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "case_id": case_id,
        "quality_action_id": qa.name,
        "quality_action_url": _qa_uri(qa.name),
        "was_existing": False,
    }
