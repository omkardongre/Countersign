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

import frappe
from frappe import _
from frappe.model.document import Document

API_ROLE = "Countersign ERPNext API Reader"


class CountersignSponsorTenantAccess(Document):
    def validate(self):
        self.tenant_id = str(self.tenant_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.tenant_id):
            frappe.throw(_("A valid Countersign tenant ID is required."), frappe.ValidationError)
        if self.user in {"Guest", "Administrator"}:
            frappe.throw(_("A dedicated API user is required."), frappe.ValidationError)

        user = frappe.db.get_value("User", self.user, ["enabled", "user_type"], as_dict=True)
        if not user or not user.enabled or user.user_type != "System User":
            frappe.throw(
                _("The mapped API user must be an enabled System User."), frappe.ValidationError
            )

        if API_ROLE not in frappe.get_roles(self.user):
            frappe.throw(
                _("The mapped API user must have the API Reader role."), frappe.ValidationError
            )

        user_record = frappe.db.get_value(
            "User",
            self.user,
            ["api_key", "api_secret"],
            as_dict=True,
        )
        if not user_record or not user_record.api_key or not user_record.api_secret:
            frappe.throw(
                _("The mapped API user must have API keys generated."), frappe.ValidationError
            )

        duplicate = frappe.db.exists(
            "Countersign Sponsor Tenant Access",
            {
                "user": self.user,
                "tenant_id": self.tenant_id,
                "company": self.company,
                "enabled": 1,
                "name": ["!=", self.name],
            },
        )
        if duplicate:
            frappe.throw(
                _(
                    "An enabled mapping for this user, tenant, and company already exists."
                ),
                frappe.ValidationError,
            )
