#!/usr/bin/env bash
# Prove tqa.countersign.internal:443 HTTP/2 TLS from a VM in the TQA VPC.
# Does not attach the callout to Agent Gateway.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

EVIDENCE="${ROOT}/infra/geap/evidence"
mkdir -p "${EVIDENCE}"

gcloud compute ssh "${TQA_PROBE_INSTANCE}" \
  --project="${PROJECT_ID}" \
  --zone="${TQA_ZONE:-${LOCATION}-b}" \
  --tunnel-through-iap \
  --command="sudo apt-get update -qq && sudo apt-get install -y -qq openssl dnsutils >/dev/null
getent hosts ${TQA_FQDN} | tee /tmp/tqa-dns.txt
openssl s_client -connect ${TQA_FQDN}:443 -servername ${TQA_FQDN} </dev/null 2>/dev/null | openssl x509 -noout -subject -ext subjectAltName | tee /tmp/tqa-tls.txt
echo OK"

gcloud compute scp \
  --project="${PROJECT_ID}" \
  --zone="${TQA_ZONE:-${LOCATION}-b}" \
  --tunnel-through-iap \
  "${TQA_PROBE_INSTANCE}:/tmp/tqa-dns.txt" "${TQA_PROBE_INSTANCE}:/tmp/tqa-tls.txt" \
  "${EVIDENCE}/" >/dev/null

if ! grep -q "${TQA_FQDN}" "${EVIDENCE}/tqa-tls.txt"; then
  echo "FAIL: TLS cert SAN/CN does not contain ${TQA_FQDN}" >&2
  exit 1
fi
echo "PASS: ${TQA_FQDN}:443 reachable over TLS from the TQA VPC"
echo "Do not set networkConfig on ${AGENT_GATEWAY} until this check is green."
