#!/usr/bin/env bash
# Deploy the sponsor-liaison A2A service to Cloud Run.
#
# Agent 8 of 8 per final_reseach/10_gap_analysis_and_win_plan.md section 4.1:
# a real, separate service from the case-workflow Runtime, with its own
# Agent Identity/Gateway binding (sponsor_liaison — see
# mcp-sponsor-liaison-status-only.yaml) so it can reach exactly one MCP
# tool, sponsor_batch_status, and nothing else.
#
# Requires --allow-unauthenticated at the Cloud Run layer, unlike the
# department MCP service: sponsor authentication is per-sponsor API key,
# checked inside the app (app/sponsor_liaison/auth.py), because the caller
# is a different company's process, never provisioned in this project's
# IAM the way the department MCP's own callers are.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

SERVICE="${SPONSOR_LIAISON_SERVICE:-countersign-sponsor-liaison}"
SPONSOR_LIAISON_SERVICE_ACCOUNT="${SPONSOR_LIAISON_SERVICE_ACCOUNT:-countersign-sponsor-liaison@${PROJECT_ID}.iam.gserviceaccount.com}"
IMAGE="${LOCATION}-docker.pkg.dev/${PROJECT_ID}/countersign/${SERVICE}:$(date -u +%Y%m%d%H%M%S)"

if [[ -z "${COUNTERSIGN_SPONSOR_API_KEYS:-}" ]]; then
  echo "Set COUNTERSIGN_SPONSOR_API_KEYS=key:sponsor_id,... before deploying." >&2
  echo "Empty means no sponsor is authorized for anything (fail-closed default)," >&2
  echo "which is safe but means this deploy would answer nothing." >&2
  exit 1
fi
if [[ -z "${COUNTERSIGN_SPONSOR_TENANT_MAP:-}" ]]; then
  echo "Set COUNTERSIGN_SPONSOR_TENANT_MAP=sponsor_id:tenant_id,... before deploying." >&2
  exit 1
fi

echo "Building ${IMAGE} from Dockerfile.sponsor-liaison"
gcloud builds submit "${ROOT}" \
  --project="${PROJECT_ID}" \
  --config="${ROOT}/infra/geap/cloudbuild-sponsor-liaison.yaml" \
  --substitutions="_IMAGE=${IMAGE}"

echo "Deploying ${SERVICE}"
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --image="${IMAGE}" \
  --port=8081 \
  --allow-unauthenticated \
  --service-account="${SPONSOR_LIAISON_SERVICE_ACCOUNT}" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_LOCATION=global,COUNTERSIGN_MCP_URL=${COUNTERSIGN_MCP_URL:?Set COUNTERSIGN_MCP_URL to the department MCP service URL},COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT=${COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT:?Set to the impersonated invoker service account},COUNTERSIGN_SPONSOR_API_KEYS=${COUNTERSIGN_SPONSOR_API_KEYS},COUNTERSIGN_SPONSOR_TENANT_MAP=${COUNTERSIGN_SPONSOR_TENANT_MAP}" \
  --min-instances=0 \
  --max-instances=2 \
  --quiet

echo "Grant roles/run.invoker on the department MCP service to"
echo "${SPONSOR_LIAISON_SERVICE_ACCOUNT}, and bind the sponsor_liaison Gateway"
echo "identity (mcp-sponsor-liaison-status-only.yaml) before this service can"
echo "actually answer a real query."
