#!/usr/bin/env bash
# GCE VM startup script: installs Docker, then runs SENAITE via
# infra/geap/senaite/compose.yml. Fetches the admin password from Secret
# Manager at boot rather than baking it into the image or metadata in
# plaintext. Re-run safe: docker compose up -d is idempotent.
set -euo pipefail

PROJECT_ID="countersign-505107"
COMPOSE_DIR="/opt/countersign/senaite"

if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg \
    -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

mkdir -p "${COMPOSE_DIR}"
curl -fsSL -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/attributes/compose-yml" \
  -o "${COMPOSE_DIR}/compose.yml"

SENAITE_ADMIN_PASSWORD="$(gcloud secrets versions access latest \
  --secret=countersign-senaite-admin-password \
  --project="${PROJECT_ID}")"
export SENAITE_ADMIN_PASSWORD

cd "${COMPOSE_DIR}"
docker compose up -d
