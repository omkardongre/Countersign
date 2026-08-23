#!/usr/bin/env bash
# Provision the real QC lab (Cloud SQL) and environmental monitoring
# (BigQuery) systems for P1. Same Cloud SQL instance as ADK sessions and
# the sterility clock (countersign-sessions), a new database on it
# (countersign_lims) so a LIMS credential problem cannot touch session
# rows. EM gets its own BigQuery dataset (countersign_em), since EM's
# columnar/append-only shape does not belong in Postgres.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/constants.env"

LIMS_DB="${LIMS_DB_NAME:-countersign_lims}"
LIMS_TABLE="${LIMS_TABLE_NAME:-lims_results}"
EM_DATASET="${EM_DATASET_NAME:-countersign_em}"
EM_TABLE="${EM_TABLE_NAME:-em_readings}"
SECRET_ID="${SESSIONS_SQL_SECRET:-countersign-sessions-postgres}"
MCP_SERVICE_ACCOUNT="${MCP_SERVICE_ACCOUNT:-countersign-department-mcp@${PROJECT_ID}.iam.gserviceaccount.com}"
# Same IAM database user deploy_department_mcp.sh already grants
# roles/cloudsql.client to implicitly via the service account's own
# identity token; this is the *database* user name for that same principal.
SQL_DB_IAM_USER="${SQL_DB_IAM_USER:-countersign-department-mcp@${PROJECT_ID}.iam}"

gcloud config set account admin@countersign.site >/dev/null
gcloud config set project "${PROJECT_ID}" >/dev/null

echo "--- QC lab: Cloud SQL ${SESSIONS_SQL_INSTANCE:-countersign-sessions}/${LIMS_DB}.${LIMS_TABLE} ---"
if ! gcloud sql users list \
    --instance="${SESSIONS_SQL_INSTANCE:-countersign-sessions}" \
    --project="${PROJECT_ID}" --format='value(name)' \
    | grep -qx "${SQL_DB_IAM_USER}"; then
  gcloud sql users create "${SQL_DB_IAM_USER}" \
    --instance="${SESSIONS_SQL_INSTANCE:-countersign-sessions}" \
    --project="${PROJECT_ID}" \
    --type=cloud_iam_service_account
fi
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${MCP_SERVICE_ACCOUNT}" \
  --role="roles/cloudsql.client" \
  --condition=None \
  --quiet >/dev/null

export INSTANCE_CONNECTION_NAME="${INSTANCE_CONNECTION_NAME}"
export LIMS_DB_NAME="${LIMS_DB}"
export LIMS_TABLE_NAME="${LIMS_TABLE}"
export SESSIONS_SQL_BOOTSTRAP_SECRET="${SECRET_ID}"
export SQL_DB_IAM_USER="${SQL_DB_IAM_USER}"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
(cd "${REPO_ROOT}" && uv run python "${SCRIPT_DIR}/bootstrap_department_systems.py")

echo "--- Environmental monitoring: BigQuery ${PROJECT_ID}:${EM_DATASET}.${EM_TABLE} ---"
gcloud services enable bigquery.googleapis.com --project="${PROJECT_ID}"

bq --project_id="${PROJECT_ID}" mk --dataset \
  --location="${LOCATION}" \
  --description="Countersign environmental monitoring readings (P1)" \
  "${PROJECT_ID}:${EM_DATASET}" 2>/dev/null || true

bq --project_id="${PROJECT_ID}" mk --table \
  "${PROJECT_ID}:${EM_DATASET}.${EM_TABLE}" \
  "tenant_id:STRING,batch_id:STRING,location_id:STRING,parameter:STRING,label:STRING,value:FLOAT64,unit:STRING,alert_limit:FLOAT64,action_limit:FLOAT64,recorded_at:TIMESTAMP" \
  2>/dev/null || true

# Seed one row matching the existing synthetic catalog (tenant aurex, batch
# AX-2291), so a demo case behaves identically whether
# COUNTERSIGN_EM_BACKEND is on or off.
bq --project_id="${PROJECT_ID}" query --use_legacy_sql=false --nouse_cache <<SQL
INSERT INTO \`${PROJECT_ID}.${EM_DATASET}.${EM_TABLE}\`
  (tenant_id, batch_id, location_id, parameter, label, value, unit, alert_limit, action_limit, recorded_at)
SELECT 'aurex', 'AX-2291', 'FILL-R1', 'viable_air_cfu', 'Viable air count', 2.0, 'cfu/m3', 5.0, 10.0, TIMESTAMP('2026-08-01T10:00:00Z')
FROM (SELECT 1)
WHERE NOT EXISTS (
  SELECT 1 FROM \`${PROJECT_ID}.${EM_DATASET}.${EM_TABLE}\`
  WHERE tenant_id = 'aurex' AND batch_id = 'AX-2291' AND location_id = 'FILL-R1'
    AND parameter = 'viable_air_cfu'
);
SQL

# BigQuery Data Viewer + Job User only: read-only, same discipline as the
# Cloud SQL SELECT-only grant above. No agent path can write a reading.
# Granted to the department MCP service account, the process that actually
# runs the query (department_source_bigquery.py) — not to a human account.
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${MCP_SERVICE_ACCOUNT}" \
  --role="roles/bigquery.dataViewer" \
  --condition=None \
  --quiet >/dev/null
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${MCP_SERVICE_ACCOUNT}" \
  --role="roles/bigquery.jobUser" \
  --condition=None \
  --quiet >/dev/null

echo
echo "QC lab and environmental monitoring are provisioned."
echo "To switch an agent onto the real system, add to deploy_department_mcp.sh's"
echo "--set-env-vars (the MCP process is what opens these connections, not the"
echo "Agent Runtime):"
echo "  COUNTERSIGN_LIMS_BACKEND=cloud_sql,COUNTERSIGN_LIMS_DB=${LIMS_DB},COUNTERSIGN_LIMS_TABLE=${LIMS_TABLE}"
echo "  COUNTERSIGN_EM_BACKEND=bigquery,COUNTERSIGN_EM_DATASET=${EM_DATASET},COUNTERSIGN_EM_TABLE=${EM_TABLE}"
echo "Unset (or any other value) keeps that department on the synthetic catalog."
