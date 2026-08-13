#!/usr/bin/env bash
# Rebuild the TQA image and replace the container on the existing callout VM.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
_TQA_ZONE_OVERRIDE="${TQA_ZONE:-}"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

ZONE="${_TQA_ZONE_OVERRIDE:-${TQA_ZONE:-${LOCATION}-c}}"
IMAGE="${TQA_IMAGE:-${TQA_IMAGE_REPO}:$(date -u +%Y%m%d%H%M%S)}"

gcloud config set account admin@countersign.site --quiet
gcloud config set project "${PROJECT_ID}" --quiet

gcloud builds submit "${ROOT}/infra/geap/tqa_spike" \
  --project="${PROJECT_ID}" \
  --tag="${IMAGE}"

gcloud compute ssh "${TQA_CALLOUT_INSTANCE}" \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" \
  --tunnel-through-iap \
  --quiet \
  --command="sudo gcloud auth configure-docker ${LOCATION}-docker.pkg.dev --quiet
sudo docker pull ${IMAGE}
sudo docker rm -f tqa-callout >/dev/null 2>&1 || true
sudo docker run -d --restart=always --name tqa-callout -p 443:443 \
  -e TQA_FQDN=${TQA_FQDN} ${IMAGE}
sudo docker ps --filter name=tqa-callout --format '{{.Status}} {{.Image}}'"

echo "Updated ${TQA_CALLOUT_INSTANCE} -> ${IMAGE}"
