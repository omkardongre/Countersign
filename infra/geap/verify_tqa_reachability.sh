#!/usr/bin/env bash
# Prove tqa.countersign.internal:443 is reachable over plain HTTP/2 (H2C)
# from a VM in the TQA VPC. Does not attach the callout to Agent Gateway.
#
# H2C, not TLS: the callout has no certificate (see
# infra/geap/tqa_spike/src/countersign_tqa/server.py module docstring). A
# self-signed cert was rejected by a real gRPC/Envoy client with
# FAILED_PRECONDITION even though it looked fine to a non-verifying
# `openssl s_client` probe, which caused the live-only MCP `tools/call` 400.
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
  --command="sudo apt-get update -qq && sudo apt-get install -y -qq dnsutils netcat-openbsd >/dev/null
getent hosts ${TQA_FQDN} | tee /tmp/tqa-dns.txt
nc -zv -w3 ${TQA_FQDN} 443 2>&1 | tee /tmp/tqa-tls.txt
echo OK"

gcloud compute scp \
  --project="${PROJECT_ID}" \
  --zone="${TQA_ZONE:-${LOCATION}-b}" \
  --tunnel-through-iap \
  "${TQA_PROBE_INSTANCE}:/tmp/tqa-dns.txt" "${TQA_PROBE_INSTANCE}:/tmp/tqa-tls.txt" \
  "${EVIDENCE}/" >/dev/null

if ! grep -qi "succeeded\|open" "${EVIDENCE}/tqa-tls.txt"; then
  echo "FAIL: ${TQA_FQDN}:443 not reachable (H2C, no TLS handshake expected)" >&2
  exit 1
fi
echo "PASS: ${TQA_FQDN}:443 reachable (H2C) from the TQA VPC"
echo "Do not set networkConfig on ${AGENT_GATEWAY} until this check is green."
