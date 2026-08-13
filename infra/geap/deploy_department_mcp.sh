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
  --quiet

echo "Deployed. Register this URL in Agent Registry and grant roles/iap.egressor"
echo "to the case Workflow Agent Identity. Do not bind the qc_probe tool-name"
echo "policy to that identity."
