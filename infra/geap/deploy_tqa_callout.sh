#!/usr/bin/env bash
# Build the hello ext_proc image and run it on a private GCE VM (host port 443).
# Registers tqa.countersign.internal in the private DNS zone.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
_TQA_ZONE_OVERRIDE="${TQA_ZONE:-}"
_TQA_MACHINE_OVERRIDE="${TQA_MACHINE:-}"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

SPIKE="${ROOT}/infra/geap/tqa_spike"
IMAGE="${TQA_IMAGE:-${TQA_IMAGE_REPO}:$(date -u +%Y%m%d%H%M%S)}"
ZONE="${_TQA_ZONE_OVERRIDE:-${TQA_ZONE:-${LOCATION}-c}}"
MACHINE="${_TQA_MACHINE_OVERRIDE:-${TQA_MACHINE:-e2-micro}}"

gcloud artifacts repositories describe countersign \
  --project="${PROJECT_ID}" --location="${LOCATION}" >/dev/null 2>&1 \
  || gcloud artifacts repositories create countersign \
       --project="${PROJECT_ID}" \
       --location="${LOCATION}" \
       --repository-format=docker \
       --quiet

if [[ "${TQA_SKIP_BUILD:-0}" != "1" ]]; then
  gcloud builds submit "${SPIKE}" \
    --project="${PROJECT_ID}" \
    --tag="${IMAGE}"
fi

STARTUP="$(mktemp)"
cat >"${STARTUP}" <<EOF
#!/bin/bash
set -euo pipefail
apt-get update -qq
apt-get install -y -qq docker.io google-cloud-cli >/dev/null
gcloud auth configure-docker ${LOCATION}-docker.pkg.dev --quiet
docker pull ${IMAGE}
docker rm -f tqa-callout >/dev/null 2>&1 || true
mkdir -p /etc/tqa
if [ -f /tmp/tqa-leaf.crt ] && [ -f /tmp/tqa-leaf.key ]; then
  mv /tmp/tqa-leaf.crt /etc/tqa/tls.crt
  mv /tmp/tqa-leaf.key /etc/tqa/tls.key
fi
docker run -d --restart=always --name tqa-callout -p 443:443 \
  -v /etc/tqa:/etc/tqa:ro \
  -e TQA_FQDN=${TQA_FQDN} ${IMAGE}
EOF

create_or_replace() {
  local name="$1"
  if gcloud compute instances describe "${name}" \
    --zone="${ZONE}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud compute instances delete "${name}" \
      --zone="${ZONE}" --project="${PROJECT_ID}" --quiet
  fi
}

create_or_replace "${TQA_CALLOUT_INSTANCE}"
gcloud compute instances create "${TQA_CALLOUT_INSTANCE}" \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" \
  --machine-type="${MACHINE}" \
  --network="${TQA_NETWORK}" \
  --subnet="${TQA_SUBNET_RUNTIME}" \
  --no-address \
  --tags=tqa-callout \
  --scopes=cloud-platform \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --metadata-from-file=startup-script="${STARTUP}"

rm -f "${STARTUP}"

if ! gcloud compute instances describe "${TQA_PROBE_INSTANCE}" \
  --zone="${ZONE}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud compute instances create "${TQA_PROBE_INSTANCE}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --machine-type="${MACHINE}" \
    --network="${TQA_NETWORK}" \
    --subnet="${TQA_SUBNET_RUNTIME}" \
    --no-address \
    --tags=tqa-probe \
    --image-family=debian-12 \
    --image-project=debian-cloud
fi

CALLOUT_IP="$(
  gcloud compute instances describe "${TQA_CALLOUT_INSTANCE}" \
    --zone="${ZONE}" --project="${PROJECT_ID}" \
    --format='get(networkInterfaces[0].networkIP)'
)"

if gcloud dns record-sets describe "${TQA_FQDN}." \
  --zone="${TQA_DNS_ZONE}" --project="${PROJECT_ID}" --type=A >/dev/null 2>&1; then
  gcloud dns record-sets delete "${TQA_FQDN}." \
    --zone="${TQA_DNS_ZONE}" --project="${PROJECT_ID}" --type=A --quiet
fi
gcloud dns record-sets create "${TQA_FQDN}." \
  --zone="${TQA_DNS_ZONE}" --project="${PROJECT_ID}" \
  --type=A --ttl=60 --rrdatas="${CALLOUT_IP}"

echo "Callout ${CALLOUT_IP} -> ${TQA_FQDN}. Next: verify_tqa_reachability.sh"
