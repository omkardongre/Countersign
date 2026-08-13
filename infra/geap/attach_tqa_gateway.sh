#!/usr/bin/env bash
# Grant DNS peer, attach VPC to the live gateway, import TQA CONTENT_AUTHZ.
# networkAttachment cannot be changed after this import succeeds.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

gcloud config set account admin@countersign.site --quiet
gcloud config set project "${PROJECT_ID}" --quiet

GW_SA="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-agentgateway.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="${GW_SA}" \
  --role=roles/dns.peer \
  --quiet >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="${GW_SA}" \
  --role=roles/compute.networkUser \
  --quiet >/dev/null

gcloud service-extensions authz-extensions import countersign-tqa \
  --source="${ROOT}/infra/geap/tqa-authz-extension.yaml" \
  --location="${LOCATION}" \
  --project="${PROJECT_ID}"

gcloud beta network-security authz-policies import countersign-tqa \
  --source="${ROOT}/infra/geap/tqa-authz-policy.yaml" \
  --location="${LOCATION}" \
  --project="${PROJECT_ID}"

gcloud network-services agent-gateways import "${AGENT_GATEWAY}" \
  --source="${ROOT}/infra/geap/agent-gateway-vpc.yaml" \
  --location="${LOCATION}" \
  --project="${PROJECT_ID}"

echo "Attached ${TQA_NETWORK_ATTACHMENT} and countersign-tqa CONTENT_AUTHZ on ${AGENT_GATEWAY}"
