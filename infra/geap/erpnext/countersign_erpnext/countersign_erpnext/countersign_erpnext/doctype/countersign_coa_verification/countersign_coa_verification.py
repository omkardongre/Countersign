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
import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, today


class CountersignCoAVerification(Document):
    def validate(self):
        self._validate_tenant_id()
        self._validate_unique_evidence()
        self._validate_source_records()
        self._validate_attachment()
        self._validate_gmp_certificate()
        self._validate_genealogy()

    def before_submit(self):
        if self.verification_status not in {"Verified", "Rejected"}:
            frappe.throw(
                _("A human Verified or Rejected outcome is required before submission."),
                frappe.ValidationError,
            )
        if bool(self.supplier_coa_verified) != (self.verification_status == "Verified"):
            frappe.throw(
                _("The supplier CoA verification flag must match the human outcome."),
                frappe.ValidationError,
            )
        if self.supplier_coa_verified:
            today_str = today()
            if str(self.retest_date) < today_str:
                frappe.throw(
                    _("A CoA with an elapsed retest date cannot be marked Verified."),
                    frappe.ValidationError,
                )
            if str(self.gmp_certificate_expiry) < today_str:
                frappe.throw(
                    _("A CoA with an expired GMP certificate cannot be marked Verified."),
                    frappe.ValidationError,
                )
        reviewed_on = now_datetime()
        self.reviewed_by = frappe.session.user
        self.reviewed_on = reviewed_on
        self.verified_by = frappe.session.user if self.supplier_coa_verified else None
        self.verified_on = reviewed_on if self.supplier_coa_verified else None

    def _validate_tenant_id(self):
        self.tenant_id = str(self.tenant_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.tenant_id):
            frappe.throw(_("A valid Countersign tenant ID is required."), frappe.ValidationError)

    def _validate_unique_evidence(self):
        duplicate = frappe.db.exists(
            "Countersign CoA Verification",
            {
                "company": self.company,
                "tenant_id": self.tenant_id,
                "material_lot": self.material_lot,
                "docstatus": ["!=", 2],
                "name": ["!=", self.name],
            },
        )
        if duplicate:
            frappe.throw(
                _(
                    "Only one active CoA verification is allowed per company, tenant, "
                    "and material lot."
                ),
                frappe.ValidationError,
            )

    def _validate_source_records(self):
        supplier = frappe.db.get_value("Supplier", self.supplier, ["disabled"], as_dict=True)
        receipt = frappe.db.get_value(
            "Purchase Receipt",
            self.purchase_receipt,
            ["docstatus", "company", "supplier"],
            as_dict=True,
        )
        material_batch = frappe.db.get_value(
            "Batch", self.material_lot, ["disabled", "item"], as_dict=True
        )
        material_item = frappe.db.get_value(
            "Item", self.raw_material_item, ["disabled"], as_dict=True
        )
        inspection = frappe.db.get_value(
            "Quality Inspection",
            self.quality_inspection,
            [
                "docstatus",
                "status",
                "reference_type",
                "reference_name",
                "company",
                "item_code",
                "batch_no",
            ],
            as_dict=True,
        )
        receipt_lot = frappe.db.sql(
            """
            SELECT receipt_item.name
            FROM `tabPurchase Receipt Item` receipt_item
            LEFT JOIN `tabSerial and Batch Entry` batch_entry
              ON batch_entry.parent = receipt_item.serial_and_batch_bundle
             AND batch_entry.batch_no = %(material_lot)s
            WHERE receipt_item.parent = %(purchase_receipt)s
              AND receipt_item.item_code = %(raw_material_item)s
              AND (receipt_item.batch_no = %(material_lot)s OR batch_entry.name IS NOT NULL)
            LIMIT 1
            """,
            {
                "material_lot": self.material_lot,
                "purchase_receipt": self.purchase_receipt,
                "raw_material_item": self.raw_material_item,
            },
        )
        valid = (
            supplier
            and not supplier.disabled
            and receipt
            and receipt.docstatus == 1
            and receipt.company == self.company
            and receipt.supplier == self.supplier
            and receipt_lot
            and material_batch
            and not material_batch.disabled
            and material_batch.item == self.raw_material_item
            and material_item
            and not material_item.disabled
            and inspection
            and inspection.docstatus == 1
            and inspection.status in {"Accepted", "Rejected"}
            and inspection.reference_type == "Purchase Receipt"
            and inspection.reference_name == self.purchase_receipt
            and inspection.company == self.company
            and inspection.item_code == self.raw_material_item
            and inspection.batch_no == self.material_lot
        )
        if not valid:
            frappe.throw(
                _("The linked source records are missing or inconsistent."),
                frappe.ValidationError,
            )

    def _validate_attachment(self):
        self.coa_content_hash, self.coa_sha256 = self._pin_private_attachment(
            self.coa_attachment,
            self.coa_content_hash,
            self.coa_sha256,
            label="CoA",
            attached_to_field="coa_attachment",
        )

    def _validate_gmp_certificate(self):
        self.gmp_certificate_sha256 = self._pin_private_attachment(
            self.gmp_certificate_attachment,
            None,
            self.gmp_certificate_sha256,
            label="GMP certificate",
            attached_to_field="gmp_certificate_attachment",
        )[1]

    def _pin_private_attachment(
        self,
        file_url,
        existing_md5,
        existing_sha256,
        *,
        label,
        attached_to_field,
    ):
        # On a brand-new (not yet inserted) document, Frappe uploads the
        # attachment before the final document name exists, so the File
        # record's attached_to_name may still hold the client-side
        # "new-..." placeholder rather than self.name at validate() time.
        # file_url + is_private + attached_to_doctype/field is already an
        # effectively unique, non-guessable binding, so attached_to_name is
        # not required for correctness here.
        # Re-attaching the same filename across separate upload attempts
        # (e.g. a corrected browser session) can leave more than one File
        # record pointing at the same file_url; the most recently created
        # one is the one currently referenced by this field.
        files = frappe.get_all(
            "File",
            filters={
                "file_url": file_url,
                "attached_to_doctype": self.doctype,
                "attached_to_field": attached_to_field,
            },
            fields=["name", "is_private", "content_hash"],
            order_by="creation desc",
            limit_page_length=1,
        )
        if len(files) != 1 or not files[0].is_private or not files[0].content_hash:
            frappe.throw(
                _(f"Exactly one private, hashed {label} attachment is required."),
                frappe.ValidationError,
            )
        # Read the raw bytes straight off disk rather than through
        # File.get_content(), which can decode text files to str for some
        # extensions -- a hash computed over re-encoded text would not
        # reliably match the hash Frappe recorded for the raw upload.
        file_record = frappe.get_doc("File", files[0].name)
        full_path = file_record.get_full_path()
        try:
            with open(full_path, "rb") as handle:
                exact_content = handle.read()
        except OSError:
            frappe.throw(
                _(f"The {label} attachment could not be read from storage."),
                frappe.ValidationError,
            )
        md5_hash = str(files[0].content_hash).lower()
        computed_md5 = hashlib.md5(exact_content, usedforsecurity=False).hexdigest()
        sha256_hash = hashlib.sha256(exact_content).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{32}", md5_hash) or md5_hash != computed_md5:
            frappe.throw(_(f"The {label} Frappe File MD5 is invalid."), frappe.ValidationError)
        if existing_md5 and existing_md5 != md5_hash:
            frappe.throw(_(f"The {label} attachment MD5 changed."), frappe.ValidationError)
        if existing_sha256 and existing_sha256 != sha256_hash:
            frappe.throw(
                _(f"The {label} attachment SHA-256 changed."), frappe.ValidationError
            )
        return md5_hash, sha256_hash

    def _validate_genealogy(self):
        if not self.finished_batches:
            frappe.throw(
                _("At least one finished batch genealogy record is required."),
                frappe.ValidationError,
            )
        seen_batches = set()
        for relation in self.finished_batches:
            if relation.finished_batch in seen_batches:
                frappe.throw(_("Finished batch genealogy must be unique."), frappe.ValidationError)
            seen_batches.add(relation.finished_batch)
            batch = frappe.db.get_value(
                "Batch", relation.finished_batch, ["disabled", "item"], as_dict=True
            )
            item = frappe.db.get_value("Item", relation.finished_item, ["disabled"], as_dict=True)
            stock_entry = frappe.db.get_value(
                "Stock Entry",
                relation.manufacture_stock_entry,
                ["docstatus", "purpose", "company"],
                as_dict=True,
            )
            traced_rows = frappe.db.sql(
                """
                SELECT
                    EXISTS(
                        SELECT 1 FROM `tabStock Entry Detail` source_detail
                        LEFT JOIN `tabSerial and Batch Entry` source_batch
                          ON source_batch.parent = source_detail.serial_and_batch_bundle
                         AND source_batch.batch_no = %(material_lot)s
                        WHERE source_detail.parent = %(stock_entry)s
                          AND source_detail.item_code = %(raw_material_item)s
                          AND source_detail.is_finished_item = 0
                          AND source_detail.transfer_qty > 0
                          AND (
                              source_detail.batch_no = %(material_lot)s
                              OR source_batch.name IS NOT NULL
                          )
                    ) AS source_row,
                    EXISTS(
                        SELECT 1 FROM `tabStock Entry Detail` finished_detail
                        LEFT JOIN `tabSerial and Batch Entry` finished_batch
                          ON finished_batch.parent = finished_detail.serial_and_batch_bundle
                         AND finished_batch.batch_no = %(finished_batch)s
                        WHERE finished_detail.parent = %(stock_entry)s
                          AND finished_detail.item_code = %(finished_item)s
                          AND finished_detail.is_finished_item = 1
                          AND finished_detail.transfer_qty > 0
                          AND (
                              finished_detail.batch_no = %(finished_batch)s
                              OR finished_batch.name IS NOT NULL
                          )
                    ) AS finished_row,
                    EXISTS(
                        SELECT 1 FROM `tabStock Entry Detail` source_detail
                        WHERE source_detail.parent = %(stock_entry)s
                          AND source_detail.item_code = %(raw_material_item)s
                          AND source_detail.is_finished_item = 0
                          AND source_detail.transfer_qty > 0
                          AND source_detail.s_warehouse IS NOT NULL
                          AND source_detail.s_warehouse <> ''
                    ) AS source_has_source_warehouse,
                    EXISTS(
                        SELECT 1 FROM `tabStock Entry Detail` finished_detail
                        WHERE finished_detail.parent = %(stock_entry)s
                          AND finished_detail.item_code = %(finished_item)s
                          AND finished_detail.is_finished_item = 1
                          AND finished_detail.transfer_qty > 0
                          AND finished_detail.t_warehouse IS NOT NULL
                          AND finished_detail.t_warehouse <> ''
                    ) AS finished_has_target_warehouse
                """,
                {
                    "finished_batch": relation.finished_batch,
                    "finished_item": relation.finished_item,
                    "material_lot": self.material_lot,
                    "raw_material_item": self.raw_material_item,
                    "stock_entry": relation.manufacture_stock_entry,
                },
                as_dict=True,
            )[0]
            valid = (
                batch
                and not batch.disabled
                and batch.item == relation.finished_item
                and item
                and not item.disabled
                and stock_entry
                and stock_entry.docstatus == 1
                and stock_entry.purpose == "Manufacture"
                and stock_entry.company == self.company
                and traced_rows.source_row
                and traced_rows.finished_row
                and traced_rows.source_has_source_warehouse
                and traced_rows.finished_has_target_warehouse
            )
            if not valid:
                frappe.throw(
                    _("Finished batch genealogy is missing or inconsistent."),
                    frappe.ValidationError,
                )
