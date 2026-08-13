#!/usr/bin/env bash
# Create the TQA spike VPC, subnets, NAT, DNS, and firewall. Idempotent.
# Does not attach networkAttachment to countersign-egress (immutable).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

if ! gcloud compute networks describe "${TQA_NETWORK}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud compute networks create "${TQA_NETWORK}" \
    --project="${PROJECT_ID}" \
    --subnet-mode=custom \
    --bgp-routing-mode=regional
fi

if ! gcloud compute networks subnets describe "${TQA_SUBNET_RUNTIME}" \
  --region="${LOCATION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud compute networks subnets create "${TQA_SUBNET_RUNTIME}" \
    --project="${PROJECT_ID}" \
    --network="${TQA_NETWORK}" \
    --region="${LOCATION}" \
    --range="${TQA_RUNTIME_RANGE}" \
    --enable-private-ip-google-access
fi

if ! gcloud compute networks subnets describe "${TQA_SUBNET_ATTACHMENT}" \
  --region="${LOCATION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud compute networks subnets create "${TQA_SUBNET_ATTACHMENT}" \
    --project="${PROJECT_ID}" \
    --network="${TQA_NETWORK}" \
    --region="${LOCATION}" \
    --range="${TQA_ATTACHMENT_RANGE}" \
    --enable-private-ip-google-access
fi

if ! gcloud compute routers describe "${TQA_NAT_ROUTER}" \
  --region="${LOCATION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud compute routers create "${TQA_NAT_ROUTER}" \
    --project="${PROJECT_ID}" \
    --region="${LOCATION}" \
    --network="${TQA_NETWORK}"
fi

if ! gcloud compute routers nats describe "${TQA_NAT}" \
  --router="${TQA_NAT_ROUTER}" --region="${LOCATION}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud compute routers nats create "${TQA_NAT}" \
    --project="${PROJECT_ID}" \
    --router="${TQA_NAT_ROUTER}" \
    --region="${LOCATION}" \
    --nat-all-subnet-ip-ranges \
    --auto-allocate-nat-external-ips
fi

if ! gcloud compute network-attachments describe "${TQA_NETWORK_ATTACHMENT}" \
  --region="${LOCATION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud compute network-attachments create "${TQA_NETWORK_ATTACHMENT}" \
    --project="${PROJECT_ID}" \
    --region="${LOCATION}" \
    --subnets="${TQA_SUBNET_ATTACHMENT}" \
    --connection-preference=ACCEPT_AUTOMATIC
fi

if ! gcloud dns managed-zones describe "${TQA_DNS_ZONE}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud dns managed-zones create "${TQA_DNS_ZONE}" \
    --project="${PROJECT_ID}" \
    --description="TQA callout private zone" \
    --dns-name="${TQA_DNS_DOMAIN}" \
    --visibility=private \
    --networks="${TQA_NETWORK}"
fi

if ! gcloud compute firewall-rules describe countersign-tqa-allow-internal \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud compute firewall-rules create countersign-tqa-allow-internal \
    --project="${PROJECT_ID}" \
    --network="${TQA_NETWORK}" \
    --allow=tcp:443,tcp:22 \
    --source-ranges="${TQA_RUNTIME_RANGE},${TQA_ATTACHMENT_RANGE}" \
    --target-tags=tqa-callout
fi

if ! gcloud compute firewall-rules describe countersign-tqa-allow-iap-ssh \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud compute firewall-rules create countersign-tqa-allow-iap-ssh \
    --project="${PROJECT_ID}" \
    --network="${TQA_NETWORK}" \
    --allow=tcp:22 \
    --source-ranges=35.235.240.0/20 \
    --target-tags=tqa-probe,tqa-callout
fi

echo "VPC ${TQA_NETWORK} ready. Next: deploy_tqa_callout.sh"
