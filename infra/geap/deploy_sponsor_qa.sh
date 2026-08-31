#!/usr/bin/env bash
# Build and deploy the separate Sponsor QA A2A receiver. Does not deploy the case Runtime.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

SERVICE="${SPONSOR_QA_SERVICE:-countersign-sponsor-qa}"
SERVICE_ACCOUNT="${SPONSOR_QA_SERVICE_ACCOUNT:-countersign-sponsor-qa@${PROJECT_ID}.iam.gserviceaccount.com}"
CALLERS_SECRET="${SPONSOR_QA_CALLERS_SECRET:-countersign-sponsor-qa-callers}"
IMAGE="${LOCATION}-docker.pkg.dev/${PROJECT_ID}/countersign/${SERVICE}:$(date -u +%Y%m%d%H%M%S)"

# Prerequisites (one-time, run by an IAM administrator):
# gcloud iam service-accounts create countersign-sponsor-qa --project "$PROJECT_ID"
# gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$SERVICE_ACCOUNT" --role roles/datastore.user
# gcloud secrets add-iam-policy-binding "$CALLERS_SECRET" --project "$PROJECT_ID" --member "serviceAccount:$SERVICE_ACCOUNT" --role roles/secretmanager.secretAccessor
# The service account needs no other data-plane role. Cloud Build/deployer roles remain on the operator, not this runtime identity.
gcloud secrets describe "${CALLERS_SECRET}" --project="${PROJECT_ID}" >/dev/null || {
  echo "Missing Secret Manager secret ${CALLERS_SECRET}. Its value must be a JSON array of tenant_id/bearer_token objects." >&2
  exit 1
}

gcloud builds submit "${ROOT}" --project="${PROJECT_ID}" \
  --config="${ROOT}/infra/geap/cloudbuild-sponsor-qa.yaml" \
  --substitutions="_IMAGE=${IMAGE}"

gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" --region="${LOCATION}" --image="${IMAGE}" --port=8082 \
  --allow-unauthenticated --service-account="${SERVICE_ACCOUNT}" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},APP_URL=https://${SERVICE}-${PROJECT_NUMBER}.${LOCATION}.run.app,COUNTERSIGN_SPONSOR_QA_DATABASE=${SPONSOR_QA_DATABASE:-(default)}" \
  --set-secrets="COUNTERSIGN_SPONSOR_QA_CALLERS_JSON=${CALLERS_SECRET}:latest" \
  --min-instances=0 --max-instances=3 --concurrency=40 --quiet

echo "Receiver deployed. Add its ${SERVICE} URL and the matching tenant token only to ${SPONSOR_OUTBOUND_TARGETS_SECRET:-countersign-sponsor-outbound-targets}; never configure either in the case Runtime."
