#!/usr/bin/env bash
# Idempotent GCE startup for the Countersign ERPNext production stack.
set -euo pipefail

readonly METADATA_URL="http://metadata.google.internal/computeMetadata/v1"
readonly METADATA_HEADER="Metadata-Flavor: Google"
readonly STACK_DIR="/opt/countersign-erpnext"
readonly SECRETS_DIR="${STACK_DIR}/secrets"
readonly COMPOSE_FILE="${STACK_DIR}/compose.yaml"

metadata() {
  curl --fail --silent --show-error \
    --header "${METADATA_HEADER}" "${METADATA_URL}/instance/attributes/$1"
}

project_id() {
  curl --fail --silent --show-error \
    --header "${METADATA_HEADER}" "${METADATA_URL}/project/project-id"
}

access_token() {
  curl --fail --silent --show-error \
    --header "${METADATA_HEADER}" \
    "${METADATA_URL}/instance/service-accounts/default/token" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
}

fetch_secret() {
  local project="$1"
  local secret="$2"
  local destination="$3"
  local token
  local temporary
  token="$(access_token)"
  temporary="$(mktemp "${SECRETS_DIR}/.secret.XXXXXX")"
  if ! curl --fail --silent --show-error \
    --header "Authorization: Bearer ${token}" \
    "https://secretmanager.googleapis.com/v1/projects/${project}/secrets/${secret}/versions/latest:access" \
    | python3 -c 'import base64,json,sys; sys.stdout.buffer.write(base64.b64decode(json.load(sys.stdin)["payload"]["data"]))' \
    >"${temporary}"; then
    rm -f "${temporary}"
    return 1
  fi
  chmod 0600 "${temporary}"
  mv -f "${temporary}" "${destination}"
}

install_runtime() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install --no-install-recommends -y ca-certificates curl docker.io docker-compose-v2 python3
  systemctl enable --now docker
}

main() {
  umask 077
  install -d -m 0700 "${STACK_DIR}" "${SECRETS_DIR}"

  local project
  local domain
  local image
  local backup_bucket
  local acme_email
  project="$(project_id)"
  domain="$(metadata erpnext-domain)"
  image="$(metadata erpnext-image)"
  backup_bucket="$(metadata erpnext-backup-bucket)"
  acme_email="$(metadata erpnext-acme-email)"

  if [[ ! "${domain}" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]; then
    echo "Invalid erpnext-domain instance metadata." >&2
    exit 1
  fi
  if [[ ! "${image}" =~ ^[a-z0-9-]+-docker\.pkg\.dev/[^[:space:]]+/countersign-erpnext:v16\.31\.1-countersign-1$ ]]; then
    echo "Refusing an unpinned or unexpected ERPNext image." >&2
    exit 1
  fi

  install_runtime
  local registry
  registry="${image%%/*}"
  access_token | docker login "https://${registry}" \
    --username oauth2accesstoken --password-stdin >/dev/null
  metadata erpnext-compose >"${COMPOSE_FILE}"
  chmod 0600 "${COMPOSE_FILE}"

  fetch_secret "${project}" "$(metadata erpnext-db-secret)" "${SECRETS_DIR}/db_password"
  fetch_secret "${project}" "$(metadata erpnext-admin-secret)" "${SECRETS_DIR}/admin_password"
  fetch_secret "${project}" "$(metadata erpnext-backup-secret)" "${SECRETS_DIR}/backup_password"

  cat >"${STACK_DIR}/.env" <<EOF
ERPNEXT_IMAGE=${image}
ERPNext_SITE=${domain}
BACKUP_BUCKET=${backup_bucket}
ACME_EMAIL=${acme_email}
SECRETS_DIR=${SECRETS_DIR}
EOF
  chmod 0600 "${STACK_DIR}/.env"

  cd "${STACK_DIR}"
  docker compose --env-file .env -f "${COMPOSE_FILE}" pull
  docker compose --env-file .env -f "${COMPOSE_FILE}" up -d \
    db redis-cache redis-queue configurator backend

  local backend_id
  backend_id="$(docker compose --env-file .env -f "${COMPOSE_FILE}" ps -q backend)"
  for _ in $(seq 1 60); do
    if [[ "$(docker inspect --format '{{.State.Health.Status}}' "${backend_id}")" == "healthy" ]]; then
      break
    fi
    sleep 5
  done
  if [[ "$(docker inspect --format '{{.State.Health.Status}}' "${backend_id}")" != "healthy" ]]; then
    echo "ERPNext backend did not become healthy." >&2
    exit 1
  fi

  # bench commands produce progress bars with \r that exceed the GCE
  # metadata script runner's 64 KB line buffer (bufio.Scanner). Redirect
  # all bench output to a log file and only echo concise status lines.
  local bench_log="${STACK_DIR}/bench-startup.log"

  if ! docker compose --env-file .env -f "${COMPOSE_FILE}" exec -T backend \
    test -f "sites/${domain}/site_config.json"; then
    echo "Creating new site ${domain}..."
    docker compose --env-file .env -f "${COMPOSE_FILE}" exec -T backend \
      bench new-site "${domain}" \
      --db-root-password "$(<"${SECRETS_DIR}/db_password")" \
      --admin-password "$(<"${SECRETS_DIR}/admin_password")" \
      --install-app erpnext \
      --no-mariadb-socket >"${bench_log}" 2>&1
    echo "Site created."
  fi

  if ! docker compose --env-file .env -f "${COMPOSE_FILE}" exec -T backend \
    bench --site "${domain}" list-apps 2>/dev/null | grep -Fxq countersign_erpnext; then
    echo "Installing countersign_erpnext app..."
    docker compose --env-file .env -f "${COMPOSE_FILE}" exec -T backend \
      bench --site "${domain}" install-app countersign_erpnext >>"${bench_log}" 2>&1
    echo "App installed."
  fi

  echo "Running migrations..."
  docker compose --env-file .env -f "${COMPOSE_FILE}" exec -T backend \
    bench --site "${domain}" migrate >>"${bench_log}" 2>&1
  echo "Migrations done."

  docker compose --env-file .env -f "${COMPOSE_FILE}" exec -T backend \
    bench --site "${domain}" set-config host_name "https://${domain}" >>"${bench_log}" 2>&1
  docker compose --env-file .env -f "${COMPOSE_FILE}" exec -T backend \
    bench --site "${domain}" set-config backup_encryption_key "$(<"${SECRETS_DIR}/backup_password")" >>"${bench_log}" 2>&1
  docker compose --env-file .env -f "${COMPOSE_FILE}" up -d \
    websocket queue-short queue-long scheduler frontend traefik backup

  echo "Countersign ERPNext startup completed for ${domain}."
}

main "$@"
