#!/usr/bin/env bash
# Register platform endpoints required for Runtime + LLM egress, then grant
# roles/iap.egressor to the hello agent identity on each endpoint.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

MEMBER_FILE="${ROOT}/deployment_metadata.json"
if [[ ! -f "${MEMBER_FILE}" ]]; then
  echo "deployment_metadata.json missing — deploy the Runtime agent first" >&2
  exit 1
fi

EFFECTIVE_IDENTITY="$(python3 - <<PY
import json
print(json.load(open("${MEMBER_FILE}"))["effective_identity"])
PY
)"
MEMBER="principal://${EFFECTIVE_IDENTITY}"
echo "Member: ${MEMBER}"

register_endpoint() {
  local name="$1"
  local url="$2"
  local display="$3"
  if gcloud agent-registry services describe "${name}" \
      --project="${PROJECT_ID}" --location="${LOCATION}" >/dev/null 2>&1; then
    echo "Endpoint service exists: ${name}"
  else
    echo "Registering endpoint ${name} -> ${url}"
    gcloud agent-registry services create "${name}" \
      --project="${PROJECT_ID}" \
      --location="${LOCATION}" \
      --display-name="${display}" \
      --endpoint-spec-type=no-spec \
      --interfaces="url=${url},protocolBinding=http-json"
  fi

  local endpoint_id
  endpoint_id="$(
    gcloud agent-registry services describe "${name}" \
      --project="${PROJECT_ID}" \
      --location="${LOCATION}" \
      --format='value(registryResource)' | awk -F/ '{print $NF}'
  )"
  echo "  registry endpoint id: ${endpoint_id}"

  gcloud beta iap web add-iam-policy-binding \
    --resource-type=agent-registry \
    --endpoint="${endpoint_id}" \
    --region="${LOCATION}" \
    --project="${PROJECT_ID}" \
    --member="${MEMBER}" \
    --role=roles/iap.egressor \
    --condition=None \
    --quiet

  echo "  granted roles/iap.egressor"
}

register_endpoint "ep-aiplatform-regional" \
  "https://${LOCATION}-aiplatform.googleapis.com" \
  "Vertex AI regional API"
register_endpoint "ep-aiplatform-mtls" \
  "https://${LOCATION}-aiplatform.mtls.googleapis.com" \
  "Vertex AI regional mTLS API"
register_endpoint "ep-aiplatform-rep" \
  "https://aiplatform.${LOCATION}.rep.googleapis.com" \
  "Vertex AI REP API"
# Gemini 3.5+ model calls resolve via the global Vertex hostnames.
register_endpoint "ep-aiplatform-global" \
  "https://aiplatform.googleapis.com" \
  "Vertex AI global API"
register_endpoint "ep-aiplatform-global-mtls" \
  "https://aiplatform.mtls.googleapis.com" \
  "Vertex AI global mTLS API"
register_endpoint "ep-aiplatform-global-host" \
  "https://global-aiplatform.googleapis.com" \
  "Vertex AI global-host API"
register_endpoint "ep-aiplatform-global-host-mtls" \
  "https://global-aiplatform.mtls.googleapis.com" \
  "Vertex AI global-host mTLS API"
register_endpoint "ep-logging" \
  "https://logging.googleapis.com" \
  "Cloud Logging API"
register_endpoint "ep-logging-mtls" \
  "https://logging.mtls.googleapis.com" \
  "Cloud Logging mTLS API"
register_endpoint "ep-telemetry" \
  "https://telemetry.googleapis.com" \
  "Cloud Telemetry API"
register_endpoint "ep-telemetry-mtls" \
  "https://telemetry.mtls.googleapis.com" \
  "Cloud Telemetry mTLS API"
register_endpoint "ep-cloudresourcemanager" \
  "https://cloudresourcemanager.googleapis.com" \
  "Cloud Resource Manager API"
register_endpoint "ep-cloudresourcemanager-mtls" \
  "https://cloudresourcemanager.mtls.googleapis.com" \
  "Cloud Resource Manager mTLS API"
# Runtime probes allowedLocations on boot. Harmless (returns 404) but under
# enforcement it becomes a DENIED row that pollutes the governance evidence,
# so register it and keep the only deny the intentional one.
register_endpoint "ep-iamcredentials" \
  "https://iamcredentials.googleapis.com" \
  "IAM Credentials API"
register_endpoint "ep-iamcredentials-mtls" \
  "https://iamcredentials.mtls.googleapis.com" \
  "IAM Credentials mTLS API"

echo "Platform endpoints registered and egress granted."
