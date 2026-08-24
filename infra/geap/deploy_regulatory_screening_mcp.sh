#!/usr/bin/env bash
# Deploy the isolated public-regulatory-screening MCP service.
# This script does not apply Agent Gateway policies. Apply
# mcp-case-workflow-regulatory-screening-allow.yaml separately after the
# service URL is confirmed. It never enables unauthenticated Cloud Run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

for arg in "$@"; do
  if [[ "${arg}" == *allow-unauthenticated* && "${arg}" != *no-allow-unauthenticated* ]]; then
    echo "Refusing --allow-unauthenticated: regulatory MCP must require identity tokens." >&2
    exit 1
  fi
done

SERVICE="${REGULATORY_MCP_SERVICE:-countersign-regulatory-screening-mcp}"
MCP_SERVICE_ACCOUNT="${REGULATORY_MCP_SERVICE_ACCOUNT:-countersign-regulatory-screening-mcp@${PROJECT_ID}.iam.gserviceaccount.com}"
INVOKER_SERVICE_ACCOUNT="${REGULATORY_MCP_INVOKER_SERVICE_ACCOUNT:?Set REGULATORY_MCP_INVOKER_SERVICE_ACCOUNT to the separate case-runtime invoker service account}"
if [[ "${MCP_SERVICE_ACCOUNT}" == "${INVOKER_SERVICE_ACCOUNT}" ]]; then
  echo "Refusing shared service/invoker account: regulatory MCP credentials must be least-privilege and separate." >&2
  exit 1
fi
IMAGE="${LOCATION}-docker.pkg.dev/${PROJECT_ID}/countersign/${SERVICE}:$(date -u +%Y%m%d%H%M%S)"

# The dedicated process has no database/Drive credentials. Its only external
# reads are the official EudraGMDP and openFDA public endpoints.
echo "Building ${IMAGE} from Dockerfile.regulatory-screening-mcp"
gcloud builds submit "${ROOT}" \
  --project="${PROJECT_ID}" \
  --config="${ROOT}/infra/geap/cloudbuild-regulatory-screening-mcp.yaml" \
  --substitutions="_IMAGE=${IMAGE}"

echo "Deploying ${SERVICE} with --no-allow-unauthenticated"
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --image="${IMAGE}" \
  --port=8080 \
  --no-allow-unauthenticated \
  --service-account="${MCP_SERVICE_ACCOUNT}" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},COUNTERSIGN_REGULATORY_SCREENING_CACHE_TTL_SECONDS=${COUNTERSIGN_REGULATORY_SCREENING_CACHE_TTL_SECONDS:-900},COUNTERSIGN_REGULATORY_SCREENING_TIMEOUT_SECONDS=${COUNTERSIGN_REGULATORY_SCREENING_TIMEOUT_SECONDS:-15}" \
  --min-instances=0 \
  --max-instances=3 \
  --quiet

echo "Granting only Cloud Run invocation on the dedicated screening service"
gcloud run services add-iam-policy-binding "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --member="serviceAccount:${INVOKER_SERVICE_ACCOUNT}" \
  --role=roles/run.invoker \
  --quiet

URL="$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --format='value(status.url)')"

echo "Deployed private screening MCP: ${URL}/mcp"
echo "Next manual steps:"
echo "  1. Confirm the URL host matches the dedicated Gateway policy host."
echo "  2. Apply the policy and binding checklist in"
echo "     infra/geap/mcp-case-workflow-regulatory-screening-allow.yaml and"
echo "     infra/geap/mcp-tool-policy-bindings.yaml."
echo "  3. Grant the case Runtime Agent Identity"
echo "     roles/iam.serviceAccountTokenCreator on ${INVOKER_SERVICE_ACCOUNT}."
echo "  4. Pass --regulatory-mcp-url=${URL}/mcp and"
echo "     --regulatory-mcp-invoker-service-account=${INVOKER_SERVICE_ACCOUNT}"
echo "     to deploy_case_runtime.py."
