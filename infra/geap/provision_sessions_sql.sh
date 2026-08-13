#!/usr/bin/env bash
# Provision Cloud SQL Postgres for ADK sessions + sterility rows.
# IAM database auth. Postgres bootstrap password lives in Secret Manager only.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/constants.env"

INSTANCE="${SESSIONS_SQL_INSTANCE:-countersign-sessions}"
DB_NAME="${SESSIONS_SQL_DB:-adk_sessions}"
IAM_USER="${SESSIONS_SQL_IAM_USER:-admin@countersign.site}"
SECRET_ID="${SESSIONS_SQL_SECRET:-countersign-sessions-postgres}"
TIER="${SESSIONS_SQL_TIER:-db-custom-1-3840}"

gcloud config set account admin@countersign.site >/dev/null
gcloud config set project "${PROJECT_ID}" >/dev/null

gcloud services enable \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  sql-component.googleapis.com \
  --project="${PROJECT_ID}"

if ! gcloud sql instances describe "${INSTANCE}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud sql instances create "${INSTANCE}" \
    --project="${PROJECT_ID}" \
    --database-version=POSTGRES_16 \
    --edition=enterprise \
    --tier="${TIER}" \
    --region="${LOCATION}" \
    --availability-type=ZONAL \
    --storage-size=10GB \
    --storage-auto-increase \
    --backup-start-time=04:00 \
    --retained-backups-count=7 \
    --database-flags=cloudsql.iam_authentication=on \
    --assign-ip \
    --deletion-protection
fi

gcloud sql databases describe "${DB_NAME}" \
  --instance="${INSTANCE}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud sql databases create "${DB_NAME}" \
    --instance="${INSTANCE}" \
    --project="${PROJECT_ID}"

if ! gcloud secrets describe "${SECRET_ID}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  python3 - <<PY
import secrets, subprocess
pw = secrets.token_urlsafe(32)
subprocess.run(
    [
        "gcloud", "secrets", "create", "${SECRET_ID}",
        "--project", "${PROJECT_ID}",
        "--replication-policy", "automatic",
        "--data-file", "-",
    ],
    input=pw.encode(),
    check=True,
)
PY
fi

POSTGRES_PASSWORD="$(gcloud secrets versions access latest --secret="${SECRET_ID}" --project="${PROJECT_ID}")"
gcloud sql users set-password postgres \
  --instance="${INSTANCE}" \
  --project="${PROJECT_ID}" \
  --password="${POSTGRES_PASSWORD}" >/dev/null

if ! gcloud sql users list --instance="${INSTANCE}" --project="${PROJECT_ID}" \
    --format='value(name)' | grep -qx "${IAM_USER}"; then
  gcloud sql users create "${IAM_USER}" \
    --instance="${INSTANCE}" \
    --project="${PROJECT_ID}" \
    --type=cloud_iam_user
fi

for role in roles/cloudsql.client roles/cloudsql.instanceUser roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="user:${IAM_USER}" \
    --role="${role}" \
    --condition=None \
    --quiet >/dev/null
done

REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export INSTANCE_CONNECTION_NAME="${PROJECT_ID}:${LOCATION}:${INSTANCE}"
export DB_NAME="${DB_NAME}"
export DB_IAM_USER="${IAM_USER}"
export SESSIONS_SQL_BOOTSTRAP_SECRET="${SECRET_ID}"
export SESSIONS_SQL_IAM_USER="${IAM_USER}"
(cd "${REPO_ROOT}" && uv run python "${SCRIPT_DIR}/bootstrap_sessions_sql.py")

echo "Cloud SQL ${INSTANCE} / ${DB_NAME} ready."
echo "Set INSTANCE_CONNECTION_NAME=${PROJECT_ID}:${LOCATION}:${INSTANCE}"
echo "Set DB_IAM_USER to the Runtime IAM database user (not postgres)."
