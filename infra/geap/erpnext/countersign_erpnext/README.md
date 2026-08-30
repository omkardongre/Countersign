# Countersign ERPNext

Version-controlled Frappe application for read-only GMP supply evidence exchange with ERPNext 16.31.1.

## Access model

Create a dedicated API user, assign only the `Countersign ERPNext API Reader` role, and create exactly one enabled `Countersign Sponsor Tenant Access` row. The row binds the authenticated API user to a Countersign `tenant_id` string and separately to the CDMO ERPNext Company. The optional sponsor Customer is metadata and is never caller-controlled scope.

The API user needs ERPNext Desk access solely to remain a System User. Its dedicated role grants no report, export, File, or source-DocType permissions; the two whitelisted endpoints enforce the tenant mapping and expose only their bounded response schema. API keys and secrets remain in Frappe authentication storage.

Quality Managers maintain and submit `Countersign CoA Verification` records and must set both the CDMO Company and the bounded Countersign `tenant_id`. API scope requires both fields to match the authenticated user's access mapping. A record pins the official Frappe File MD5 and an independently computed SHA-256 of the exact private `File.get_content()` bytes. It links the Supplier, submitted Purchase Receipt, raw-material Item and Batch, submitted Quality Inspection, and submitted Manufacture Stock Entries proving raw-material consumption and finished-batch production.

A submitted verification records either a `Verified` or `Rejected` human outcome. Rejected CoAs, rejected submitted Quality Inspections, elapsed retest dates, and expired GMP certificates remain readable so Countersign can create deterministic findings. Missing, ambiguous, cancelled, disabled, public, hash-changed, unsubmitted, or inconsistent evidence fails closed.

## API

Authenticated `GET` requests only:

- `/api/method/countersign_erpnext.api.v1.get_coa_snapshot?batch_id=<finished Batch name>`
- `/api/method/countersign_erpnext.api.v1.get_batches_sharing_material_lot?batch_id=<finished Batch name>`

Both methods resolve exactly one active CoA verification through the requested finished Batch's genealogy. The sibling method returns every other represented finished Batch consuming the same raw-material lot and excludes the requested source Batch.

Responses use schema `countersign.erpnext.supply.v1` and the exact Countersign adapter field contract. `tenant_id` is resolved only from the authenticated API user's server-side mapping. `source_uri` points to the HTTPS Desk route `/app/countersign-coa-verification/<record id>`.

The APIs never expose attachment URLs, paths, MD5 values, bytes, or extracted text. They return only the private File identifier and pinned SHA-256 required by the adapter.
