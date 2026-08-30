#!/usr/bin/env bash
# Post-setup: create the Countersign API user in ERPNext, generate API keys,
# and store the credentials JSON in Secret Manager for the department MCP.
#
# Prerequisites:
#   - ERPNext is running and healthy on the VM
#   - The admin password secret exists in Secret Manager
#   - gcloud and curl are available locally
#
# Usage:
#   ERPNEXT_DOMAIN=erp.example.com \
#   TENANT_ID=ACME01 \
#   COMPANY="CDMO Company" \
#   PROJECT_ID=countersign-505107 \
#   API_CREDENTIALS_SECRET=countersign-erpnext-credentials \
#   bash post_setup.sh
set -euo pipefail

DOMAIN="${ERPNEXT_DOMAIN:?Set ERPNEXT_DOMAIN}"
TENANT_ID="${TENANT_ID:?Set TENANT_ID}"
COMPANY="${COMPANY:?Set COMPANY}"
PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
API_CREDENTIALS_SECRET="${API_CREDENTIALS_SECRET:-countersign-erpnext-credentials}"
ADMIN_SECRET="${ERPNEXT_ADMIN_SECRET:-countersign-erpnext-admin-password}"
API_USER_EMAIL="${API_USER_EMAIL:-countersign-api@${DOMAIN}}"
API_USER_FULL_NAME="${API_USER_FULL_NAME:-Countersign API Reader}"
API_ROLE="Countersign ERPNext API Reader"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Required command not found: $1" >&2
    exit 1
  }
}

fetch_secret() {
  gcloud secrets versions access latest \
    --secret="$1" --project="${PROJECT_ID}"
}

erpnext_api() {
  local path="$1"
  local method="${2:-GET}"
  local data="${3:-}"
  local admin_password
  admin_password="$(fetch_secret "${ADMIN_SECRET}")"
  local url="https://${DOMAIN}${path}"
  local args=(
    --fail --silent --show-error
    --request "${method}"
    --header "Content-Type: application/json"
    --header "Accept: application/json"
    --user "administrator:${admin_password}"
  )
  if [[ -n "${data}" ]]; then
    args+=("--data" "${data}")
  fi
  curl "${args[@]}" "${url}"
}

wait_for_erpnext() {
  echo "Waiting for ERPNext at https://${DOMAIN}..."
  for attempt in $(seq 1 60); do
    if curl --fail --silent --show-error \
      --output /dev/null \
      "https://${DOMAIN}/api/method/ping" 2>/dev/null; then
      echo "ERPNext is responding."
      return 0
    fi
    echo "  attempt ${attempt}/60: not ready yet, retrying in 10s..."
    sleep 10
  done
  echo "ERPNext did not become available." >&2
  exit 1
}

create_api_user() {
  echo "Creating API user ${API_USER_EMAIL}..."
  local user_data
  user_data=$(cat <<EOF
{
  "email": "${API_USER_EMAIL}",
  "first_name": "${API_USER_FULL_NAME}",
  "enabled": 1,
  "user_type": "System User",
  "roles": [
    {"role": "${API_ROLE}"}
  ]
}
EOF
)
  erpnext_api "/api/resource/User/${API_USER_EMAIL}" "POST" "${user_data}" \
    >/dev/null 2>&1 || true

  echo "Generating API keys for ${API_USER_EMAIL}..."
  erpnext_api "/api/resource/User/${API_USER_EMAIL}" "PUT" \
    '{"api_generate_new_keys": 1}' >/dev/null
}

read_api_keys() {
  echo "Reading API keys..."
  local response
  response="$(erpnext_api "/api/resource/User/${API_USER_EMAIL}" "GET")"
  API_KEY="$(echo "${response}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["api_key"])')"
  API_SECRET="$(echo "${response}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["api_secret"])')"

  if [[ -z "${API_KEY}" || -z "${API_SECRET}" ]]; then
    echo "Failed to read API keys." >&2
    exit 1
  fi
}

create_tenant_access() {
  echo "Creating Countersign Sponsor Tenant Access for tenant ${TENANT_ID}..."
  local access_data
  access_data=$(cat <<EOF
{
  "user": "${API_USER_EMAIL}",
  "tenant_id": "${TENANT_ID}",
  "company": "${COMPANY}",
  "enabled": 1
}
EOF
)
  erpnext_api \
    "/api/resource/Countersign%20Sponsor%20Tenant%20Access" \
    "POST" "${access_data}" >/dev/null
}

store_credentials() {
  echo "Storing credentials JSON in Secret Manager..."
  local credentials_json
  credentials_json=$(cat <<EOF
{
  "${TENANT_ID}": {
    "base_url": "https://${DOMAIN}",
    "api_key": "${API_KEY}",
    "api_secret": "${API_SECRET}"
  }
}
EOF
)
  printf '%s' "${credentials_json}" | gcloud secrets versions add \
    "${API_CREDENTIALS_SECRET}" \
    --project="${PROJECT_ID}" \
    --data-file=- >/dev/null
  echo "Credentials stored in secret ${API_CREDENTIALS_SECRET}."
}

verify_api() {
  echo "Verifying API access..."
  local response
  response="$(curl --fail --silent --show-error \
    --header "Accept: application/json" \
    --header "Authorization: token ${API_KEY}:${API_SECRET}" \
    "https://${DOMAIN}/api/method/frappe.auth.get_logged_user")"
  local user
  user="$(echo "${response}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["message"])')"
  if [[ "${user}" != "${API_USER_EMAIL}" ]]; then
    echo "API identity mismatch: expected ${API_USER_EMAIL}, got ${user}" >&2
    exit 1
  fi
  echo "API verified: authenticated as ${user}."
}

main() {
  require_command gcloud
  require_command curl
  require_command python3

  wait_for_erpnext
  create_api_user
  read_api_keys
  create_tenant_access
  store_credentials
  verify_api

  echo ""
  echo "Post-setup complete."
  echo "  Tenant:       ${TENANT_ID}"
  echo "  Company:      ${COMPANY}"
  echo "  API user:     ${API_USER_EMAIL}"
  echo "  Secret:       ${API_CREDENTIALS_SECRET}"
  echo ""
  echo "The department MCP can now read from this ERPNext instance."
  echo "Set COUNTERSIGN_COA_BACKEND=erpnext and"
  echo "COUNTERSIGN_SIBLING_BATCH_BACKEND=erpnext in the MCP deployment."
}

main "$@"
