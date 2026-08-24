#!/usr/bin/env bash
# Deploy the department MCP server to Cloud Run with authentication required.
# Does not apply Gateway policies and does not claim a live 403 proof.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

for arg in "$@"; do
  if [[ "${arg}" == *allow-unauthenticated* && "${arg}" != *no-allow-unauthenticated* ]]; then
    echo "Refusing --allow-unauthenticated: department MCP must require identity tokens." >&2
    exit 1
  fi
done

SERVICE="${MCP_SERVICE:-countersign-department-mcp}"
MCP_SERVICE_ACCOUNT="${MCP_SERVICE_ACCOUNT:-countersign-department-mcp@${PROJECT_ID}.iam.gserviceaccount.com}"
SQL_INSTANCE_CONNECTION_NAME="${SQL_INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${LOCATION}:${SESSIONS_SQL_INSTANCE}}"
SQL_DB_IAM_USER="${SQL_DB_IAM_USER:-countersign-department-mcp@${PROJECT_ID}.iam}"
IMAGE="${LOCATION}-docker.pkg.dev/${PROJECT_ID}/countersign/${SERVICE}:$(date -u +%Y%m%d%H%M%S)"

echo "Building ${IMAGE} from Dockerfile.mcp"
gcloud builds submit "${ROOT}" \
  --project="${PROJECT_ID}" \
  --config="${ROOT}/infra/geap/cloudbuild-department-mcp.yaml" \
  --substitutions="_IMAGE=${IMAGE}"

echo "Deploying ${SERVICE} with --no-allow-unauthenticated"
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --image="${IMAGE}" \
  --port=8080 \
  --no-allow-unauthenticated \
  --service-account="${MCP_SERVICE_ACCOUNT}" \
  --set-env-vars="INSTANCE_CONNECTION_NAME=${SQL_INSTANCE_CONNECTION_NAME},DB_NAME=${SESSIONS_SQL_DB},DB_IAM_USER=${SQL_DB_IAM_USER},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_GENAI_USE_VERTEXAI=true,COUNTERSIGN_CASE_MEMORY_DATABASE=${CASE_MEMORY_DATABASE:-(default)},COUNTERSIGN_CASE_MEMORY_COLLECTION=${CASE_MEMORY_COLLECTION:-case_memory_facts},COUNTERSIGN_CASE_MEMORY_BACKEND=${COUNTERSIGN_CASE_MEMORY_BACKEND:-},COUNTERSIGN_CASE_MEMORY_BQ_DATASET=${COUNTERSIGN_CASE_MEMORY_BQ_DATASET:-countersign_case_memory},COUNTERSIGN_CASE_MEMORY_BQ_TABLE=${COUNTERSIGN_CASE_MEMORY_BQ_TABLE:-case_memory_facts},COUNTERSIGN_LIMS_BACKEND=${COUNTERSIGN_LIMS_BACKEND:-},COUNTERSIGN_LIMS_DB=${COUNTERSIGN_LIMS_DB:-countersign_lims},COUNTERSIGN_LIMS_TABLE=${COUNTERSIGN_LIMS_TABLE:-lims_results},COUNTERSIGN_EM_BACKEND=${COUNTERSIGN_EM_BACKEND:-},COUNTERSIGN_EM_DATASET=${COUNTERSIGN_EM_DATASET:-countersign_em},COUNTERSIGN_EM_TABLE=${COUNTERSIGN_EM_TABLE:-em_readings},COUNTERSIGN_SIBLING_BATCH_BACKEND=${COUNTERSIGN_SIBLING_BATCH_BACKEND:-},COUNTERSIGN_SIBLING_BATCH_TABLE=${COUNTERSIGN_SIBLING_BATCH_TABLE:-batch_material_lots},COUNTERSIGN_SPONSOR_TENANT_MAP=${COUNTERSIGN_SPONSOR_TENANT_MAP:-},COUNTERSIGN_QMS_REPORTS_FOLDER_ID=${COUNTERSIGN_QMS_REPORTS_FOLDER_ID:-}" \
  --min-instances=0 \
  --max-instances=3 \
  --quiet

echo "Granting Case Memory IAM (Firestore + Vertex embeddings)"
for role in roles/datastore.user roles/aiplatform.user; do
  granted=0
  for attempt in 1 2 3 4 5; do
    if gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${MCP_SERVICE_ACCOUNT}" \
      --role="${role}" \
      --condition=None \
      --quiet; then
      granted=1
      break
    fi
    echo "IAM bind ${role} conflicted, retry ${attempt}"
    sleep $((attempt * 2))
  done
  if [[ "${granted}" -ne 1 ]]; then
    echo "Failed to grant ${role}" >&2
    exit 1
  fi
done

echo "Deployed. Register this URL in Agent Registry and grant roles/iap.egressor"
echo "to the case Workflow Agent Identity. Do not bind the qc_probe tool-name"
echo "policy to that identity."
echo "COUNTERSIGN_QMS_REPORTS_FOLDER_ID should be the '03 Deviation Reports' folder"
echo "under the shared Countersign QMS tree; if it is unset, qms_report_write fails closed."
echo "When set, ${MCP_SERVICE_ACCOUNT} must hold Editor (not just reader)"
echo "on that folder, or qms_report_write fails closed."
echo "Firestore Native database and vector indexes are provisioned by"
echo "infra/geap/provision_case_memory.sh (tenant_id + redacted pre-filter"
echo "on the embedding index, plus the recency fallback index)."
echo "COUNTERSIGN_CASE_MEMORY_BACKEND=bigquery switches Case Memory onto real"
echo "BigQuery VECTOR_SEARCH (Doc 12 T1-4) instead of Firestore. Provision"
echo "and grant its own IAM with infra/geap/provision_case_memory_bigquery.sh"
echo "before setting this; the Firestore roles granted above are unused by"
echo "that backend but harmless to also hold."
