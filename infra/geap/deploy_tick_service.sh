#!/usr/bin/env bash
# Deploy the case FastAPI service (app.fast_api_app) to Cloud Run. This is
# the host for POST /scheduler/sterility-tick, the OIDC-guarded endpoint
# Cloud Scheduler calls to complete due sterility holds and resume the
# parked case invocation on Agent Runtime.
#
# Requires COUNTERSIGN_CASE_ENGINE (the deployed case Agent Runtime
# resource) so a completed tick resumes the real deployment rather than an
# in-process Runner that the scheduler service never runs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

SERVICE="${TICK_SERVICE:-countersign-tick-service}"
MCP_SERVICE="${MCP_SERVICE:-countersign-department-mcp}"
TICK_SERVICE_ACCOUNT="${TICK_SERVICE_ACCOUNT:-countersign-tick-service@${PROJECT_ID}.iam.gserviceaccount.com}"
SCHEDULER_INVOKER_SERVICE_ACCOUNT="${SCHEDULER_INVOKER_SERVICE_ACCOUNT:-countersign-scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com}"
SQL_INSTANCE_CONNECTION_NAME="${SQL_INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${LOCATION}:${SESSIONS_SQL_INSTANCE}}"
SQL_DB_IAM_USER="${SQL_DB_IAM_USER:-countersign-tick-service@${PROJECT_ID}.iam}"
CASE_ENGINE="${COUNTERSIGN_CASE_ENGINE:?Set COUNTERSIGN_CASE_ENGINE to the deployed case reasoningEngine resource}"
IMAGE="${LOCATION}-docker.pkg.dev/${PROJECT_ID}/countersign/${SERVICE}:$(date -u +%Y%m%d%H%M%S)"

# CFI Phase 2 tick config. These are required in a production scheduler
# deployment: the route must never resume the wrong Runtime or silently
# fall back to a local process, and the MCP call must have an explicit
# impersonation identity and bounded lease/batch settings.
CFI_ENGINE="${COUNTERSIGN_CFI_ENGINE:?Set COUNTERSIGN_CFI_ENGINE to the deployed CFI reasoningEngine resource}"
CFI_MCP_URL="${COUNTERSIGN_MCP_URL:?Set COUNTERSIGN_MCP_URL to the department MCP service URL (…/mcp)}"
CFI_MCP_INVOKER_SERVICE_ACCOUNT="${COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT:?Set COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT to the dedicated service account the tick service impersonates for the department MCP OIDC token}"
CFI_TICK_MAX_INTERVIEWS="${COUNTERSIGN_CFI_TICK_MAX_INTERVIEWS:?Set COUNTERSIGN_CFI_TICK_MAX_INTERVIEWS to a bounded positive integer}"
CFI_TICK_LEASE_SECONDS="${COUNTERSIGN_CFI_TICK_LEASE_SECONDS:?Set COUNTERSIGN_CFI_TICK_LEASE_SECONDS to a bounded positive integer}"

echo "Building ${IMAGE} from Dockerfile"
gcloud builds submit "${ROOT}" \
  --project="${PROJECT_ID}" \
  --tag="${IMAGE}"

echo "Deploying ${SERVICE} with --no-allow-unauthenticated"
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --image="${IMAGE}" \
  --port=8080 \
  --no-allow-unauthenticated \
  --service-account="${TICK_SERVICE_ACCOUNT}" \
  --set-env-vars="INSTANCE_CONNECTION_NAME=${SQL_INSTANCE_CONNECTION_NAME},DB_NAME=${SESSIONS_SQL_DB},DB_IAM_USER=${SQL_DB_IAM_USER},COUNTERSIGN_CASE_ENGINE=${CASE_ENGINE},COUNTERSIGN_CFI_ENGINE=${CFI_ENGINE},COUNTERSIGN_MCP_URL=${CFI_MCP_URL},COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT=${CFI_MCP_INVOKER_SERVICE_ACCOUNT},COUNTERSIGN_CFI_TICK_MAX_INTERVIEWS=${CFI_TICK_MAX_INTERVIEWS},COUNTERSIGN_CFI_TICK_LEASE_SECONDS=${CFI_TICK_LEASE_SECONDS},COUNTERSIGN_CFI_START_MAX=${COUNTERSIGN_CFI_START_MAX:-10},COUNTERSIGN_CFI_START_LEASE_SECONDS=${COUNTERSIGN_CFI_START_LEASE_SECONDS:-300},COUNTERSIGN_CFI_START_MAX_ATTEMPTS=${COUNTERSIGN_CFI_START_MAX_ATTEMPTS:-5}" \
  --memory=1Gi \
  --min-instances=0 \
  --max-instances=2 \
  --quiet

# gcloud's status.url reports the vanity run.app hostname, which can change
# independently of the service. The Cloud Scheduler job's OIDC audience must
# stay stable across redeploys, so build it from the stable
# PROJECT_NUMBER.region.run.app hostname instead.
SERVICE_URL="https://${SERVICE}-${PROJECT_NUMBER}.${LOCATION}.run.app"
TICK_URL="${SERVICE_URL}/scheduler/sterility-tick"

CFI_TICK_URL="${SERVICE_URL}/scheduler/cfi-tick"
echo "Setting COUNTERSIGN_SCHEDULER_AUDIENCE/_SERVICE_ACCOUNTS now that the URL is known"
# Both tick routes share one dependency (app/scheduler/auth.py), so the
# audience allow-list carries both this service's sterility-tick URL and its
# cfi-tick URL — each is invoked by its own Cloud Scheduler job with its own
# --oidc-token-audience. COUNTERSIGN_SCHEDULER_AUDIENCE's own value contains
# a comma (app/scheduler/auth.py splits on it), so this uses gcloud's
# custom-delimiter env-vars syntax (^;^) rather than the default comma
# separator between KEY=VALUE pairs.
gcloud run services update "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --update-env-vars="^;^COUNTERSIGN_SCHEDULER_AUDIENCE=${TICK_URL},${CFI_TICK_URL};COUNTERSIGN_SCHEDULER_SERVICE_ACCOUNTS=${SCHEDULER_INVOKER_SERVICE_ACCOUNT}" \
  --quiet

echo "Granting roles/run.invoker to the scheduler invoker service account"
gcloud run services add-iam-policy-binding "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --member="serviceAccount:${SCHEDULER_INVOKER_SERVICE_ACCOUNT}" \
  --role="roles/run.invoker" \
  --quiet

echo "Granting ${CFI_MCP_INVOKER_SERVICE_ACCOUNT} roles/run.invoker on ${MCP_SERVICE}"
# The scheduler calls the private department MCP directly; Agent Gateway
# policies apply only to Agent Identity egress and cannot grant this Cloud
# Run IAM edge. The OIDC token minted by the tick service is for this
# dedicated invoker identity, so it needs run.invoker on the MCP service.
gcloud run services add-iam-policy-binding "${MCP_SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --member="serviceAccount:${CFI_MCP_INVOKER_SERVICE_ACCOUNT}" \
  --role="roles/run.invoker" \
  --condition=None \
  --quiet

echo "Granting ${TICK_SERVICE_ACCOUNT} permission to impersonate ${CFI_MCP_INVOKER_SERVICE_ACCOUNT}"
echo "(app/mcp/client.py mints the department MCP OIDC token as this account)"
gcloud iam service-accounts add-iam-policy-binding "${CFI_MCP_INVOKER_SERVICE_ACCOUNT}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${TICK_SERVICE_ACCOUNT}" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --condition=None \
  --quiet

echo "Deployed. Service URL: ${SERVICE_URL}"
echo "Tick endpoint: ${TICK_URL}"
echo "CFI tick endpoint: ${SERVICE_URL}/scheduler/cfi-tick"
echo "Create the Cloud Scheduler job with --oidc-service-account-email=${SCHEDULER_INVOKER_SERVICE_ACCOUNT}"
echo "and --oidc-token-audience=${TICK_URL} against --uri=${TICK_URL}."
echo "For the CFI tick, create a second Cloud Scheduler job with the same"
echo "invoker but --uri=${SERVICE_URL}/scheduler/cfi-tick and"
echo "--oidc-token-audience=${SERVICE_URL}/scheduler/cfi-tick."
echo "Redeploy countersign-department-mcp with"
echo "COUNTERSIGN_CFI_TICK_CALLER_SERVICE_ACCOUNTS=${CFI_MCP_INVOKER_SERVICE_ACCOUNT}"
echo "or cfi_interview_tick will fail closed for this service."
