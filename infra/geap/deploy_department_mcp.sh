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
  --set-env-vars="INSTANCE_CONNECTION_NAME=${SQL_INSTANCE_CONNECTION_NAME},DB_NAME=${SESSIONS_SQL_DB},DB_IAM_USER=${SQL_DB_IAM_USER},GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --min-instances=0 \
  --max-instances=3 \
  --quiet

echo "Granting roles/datastore.user for Case Memory (Firestore native mode)"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${MCP_SERVICE_ACCOUNT}" \
  --role="roles/datastore.user" \
  --condition=None \
  --quiet

echo "Deployed. Register this URL in Agent Registry and grant roles/iap.egressor"
echo "to the case Workflow Agent Identity. Do not bind the qc_probe tool-name"
echo "policy to that identity."
echo "If firestore.googleapis.com is not yet enabled, or no Firestore Native"
echo "database exists in this project, run:"
echo "  gcloud services enable firestore.googleapis.com --project=${PROJECT_ID}"
echo "  gcloud firestore databases create --project=${PROJECT_ID} --location=${LOCATION} --type=firestore-native"
echo "and create the vector index Case Memory needs for prior-art retrieval"
echo "(text-embedding-005 is 768-dimensional):"
echo "  gcloud firestore indexes composite create --project=${PROJECT_ID} \\"
echo "    --collection-group=case_memory_facts \\"
echo "    --field-config=vector-config='{\"dimension\":768,\"flat\":{}}',field-path=embedding"
