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
TICK_SERVICE_ACCOUNT="${TICK_SERVICE_ACCOUNT:-countersign-tick-service@${PROJECT_ID}.iam.gserviceaccount.com}"
SCHEDULER_INVOKER_SERVICE_ACCOUNT="${SCHEDULER_INVOKER_SERVICE_ACCOUNT:-countersign-scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com}"
SQL_INSTANCE_CONNECTION_NAME="${SQL_INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${LOCATION}:${SESSIONS_SQL_INSTANCE}}"
SQL_DB_IAM_USER="${SQL_DB_IAM_USER:-countersign-tick-service@${PROJECT_ID}.iam}"
CASE_ENGINE="${COUNTERSIGN_CASE_ENGINE:?Set COUNTERSIGN_CASE_ENGINE to the deployed case reasoningEngine resource}"
IMAGE="${LOCATION}-docker.pkg.dev/${PROJECT_ID}/countersign/${SERVICE}:$(date -u +%Y%m%d%H%M%S)"

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
  --set-env-vars="INSTANCE_CONNECTION_NAME=${SQL_INSTANCE_CONNECTION_NAME},DB_NAME=${SESSIONS_SQL_DB},DB_IAM_USER=${SQL_DB_IAM_USER},COUNTERSIGN_CASE_ENGINE=${CASE_ENGINE}" \
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

echo "Setting COUNTERSIGN_SCHEDULER_AUDIENCE/_SERVICE_ACCOUNTS now that the URL is known"
gcloud run services update "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --update-env-vars="COUNTERSIGN_SCHEDULER_AUDIENCE=${TICK_URL},COUNTERSIGN_SCHEDULER_SERVICE_ACCOUNTS=${SCHEDULER_INVOKER_SERVICE_ACCOUNT}" \
  --quiet

echo "Granting roles/run.invoker to the scheduler invoker service account"
gcloud run services add-iam-policy-binding "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --member="serviceAccount:${SCHEDULER_INVOKER_SERVICE_ACCOUNT}" \
  --role="roles/run.invoker" \
  --quiet

echo "Deployed. Service URL: ${SERVICE_URL}"
echo "Tick endpoint: ${TICK_URL}"
echo "Create the Cloud Scheduler job with --oidc-service-account-email=${SCHEDULER_INVOKER_SERVICE_ACCOUNT}"
echo "and --oidc-token-audience=${TICK_URL} against --uri=${TICK_URL}."
