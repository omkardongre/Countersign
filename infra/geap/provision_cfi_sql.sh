#!/usr/bin/env bash
# Provision the durable CFI aggregate and its least-privilege MCP grant.
#
# CFI state lives in the existing adk_sessions database on the sessions
# instance, alongside sterility_incubation and case_status. The CFI table is
# separate, and the department MCP service account receives only the
# SELECT/INSERT/UPDATE rights needed by cfi_get/cfi_open/cfi_save.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/constants.env"

INSTANCE_CONNECTION_NAME="${INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${LOCATION}:${SESSIONS_SQL_INSTANCE}}"
CFI_DB_NAME="${CFI_DB_NAME:-${SESSIONS_SQL_DB}}"
CFI_SQL_IAM_USER="${CFI_SQL_IAM_USER:-countersign-department-mcp@${PROJECT_ID}.iam}"
CFI_SQL_BOOTSTRAP_SECRET="${CFI_SQL_BOOTSTRAP_SECRET:-countersign-sessions-postgres}"
MCP_SERVICE_ACCOUNT="${MCP_SERVICE_ACCOUNT:-countersign-department-mcp@${PROJECT_ID}.iam.gserviceaccount.com}"

if ! gcloud sql users list \
    --instance="${SESSIONS_SQL_INSTANCE}" \
    --project="${PROJECT_ID}" \
    --format='value(name)' | grep -qx "${CFI_SQL_IAM_USER}"; then
  gcloud sql users create "${CFI_SQL_IAM_USER}" \
    --instance="${SESSIONS_SQL_INSTANCE}" \
    --project="${PROJECT_ID}" \
    --type=cloud_iam_service_account
fi

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${MCP_SERVICE_ACCOUNT}" \
  --role="roles/cloudsql.client" \
  --condition=None \
  --quiet >/dev/null

export PROJECT_ID INSTANCE_CONNECTION_NAME CFI_DB_NAME CFI_SQL_IAM_USER CFI_SQL_BOOTSTRAP_SECRET
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
(cd "${REPO_ROOT}" && uv run python "${SCRIPT_DIR}/bootstrap_cfi_sql.py")

echo
echo "CFI Cloud SQL is provisioned: ${CFI_DB_NAME}.cfi_investigation"
echo "Runtime access remains MCP-only; the MCP identity has no CFI DELETE or DDL grant."
