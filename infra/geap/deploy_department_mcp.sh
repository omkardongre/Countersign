#!/usr/bin/env bash
# Deploy the department MCP server to Cloud Run with authentication required.
# Does not apply Gateway policies and does not claim a live 403 proof.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

for arg in "$@"; do
  if [[ "${arg}" == *allow-unauthenticated* && "${arg}" != *no-allow-unauthenticated* ]]; then
    echo "Refusing --allow-unauthenticated: department MCP must require identity tokens." >&2
    exit 1
  fi
done

SERVICE="${MCP_SERVICE:-countersign-department-mcp}"
MCP_SERVICE_ACCOUNT="${MCP_SERVICE_ACCOUNT:-countersign-department-mcp@${PROJECT_ID}.iam.gserviceaccount.com}"
SQL_INSTANCE_CONNECTION_NAME="${SQL_INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${LOCATION}:${SESSIONS_SQL_INSTANCE}}"
SQL_DB_IAM_USER="${SQL_DB_IAM_USER:-countersign-department-mcp@${PROJECT_ID}.iam}"
IMAGE="${LOCATION}-docker.pkg.dev/${PROJECT_ID}/countersign/${SERVICE}:$(date -u +%Y%m%d%H%M%S)"

# CFI Phase 2 Chat interview config. Required, fail-closed — no empty
# default: cfi_interview_chat.py refuses to run without a real tenant->space
# and tenant->responder mapping, a bounded page count, deadline, and nudge
# policy. See app/domain/cfi_interview_chat.py for validation.
COUNTERSIGN_CFI_CHAT_SPACE_MAP_JSON="${COUNTERSIGN_CFI_CHAT_SPACE_MAP_JSON:?Set COUNTERSIGN_CFI_CHAT_SPACE_MAP_JSON, a JSON object mapping tenant_id -> spaces/... Chat space name}"
COUNTERSIGN_CFI_CHAT_RESPONDER_MAP_JSON="${COUNTERSIGN_CFI_CHAT_RESPONDER_MAP_JSON:?Set COUNTERSIGN_CFI_CHAT_RESPONDER_MAP_JSON, a JSON object mapping tenant_id -> users/... responder resource names}"
COUNTERSIGN_CFI_CHAT_MAX_PAGES="${COUNTERSIGN_CFI_CHAT_MAX_PAGES:?Set COUNTERSIGN_CFI_CHAT_MAX_PAGES to a bounded positive page count}"
COUNTERSIGN_CFI_INTERVIEW_DEADLINE_SECONDS="${COUNTERSIGN_CFI_INTERVIEW_DEADLINE_SECONDS:?Set COUNTERSIGN_CFI_INTERVIEW_DEADLINE_SECONDS to the interview deadline in seconds}"
COUNTERSIGN_CFI_MAX_NUDGES="${COUNTERSIGN_CFI_MAX_NUDGES:?Set COUNTERSIGN_CFI_MAX_NUDGES to the bounded nudge count}"
COUNTERSIGN_CFI_NUDGE_INTERVAL_SECONDS="${COUNTERSIGN_CFI_NUDGE_INTERVAL_SECONDS:?Set COUNTERSIGN_CFI_NUDGE_INTERVAL_SECONDS to the bounded nudge interval in seconds}"

# Scheduler control-plane caller allow-list for cfi_interview_tick. This is
# the actual enforcement boundary for that tool (see
# department_server.py::_require_cfi_tick_caller) because the scheduler tick
# service calls this Cloud Run service directly and never crosses Agent
# Gateway. app/mcp/client.py mints the OIDC token presented here as
# COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT (impersonated), not the tick
# service's own runtime service account, so this allow-list must name that
# invoker account's email, matching whatever deploy_tick_service.sh sets
# COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT to.
COUNTERSIGN_CFI_TICK_CALLER_SERVICE_ACCOUNTS="${COUNTERSIGN_CFI_TICK_CALLER_SERVICE_ACCOUNTS:?Set to the exact email of the tick service COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT value (the identity that mints the OIDC token presented to this service); comma-separated if more than one}"

# CFI Phase 3 artifact policy. Calendar and Drive folders are tenant-owned
# server configuration; no Runtime/model argument can select them. Require
# explicit production values so a deployed MCP cannot silently skip the site
# review or file a report into an unscoped/default folder.
COUNTERSIGN_CFI_CALENDAR_MAP_JSON="${COUNTERSIGN_CFI_CALENDAR_MAP_JSON:?Set a JSON object mapping tenant_id to the dedicated CFI Calendar id}"
COUNTERSIGN_CFI_CALENDAR_ATTENDEE_MAP_JSON="${COUNTERSIGN_CFI_CALENDAR_ATTENDEE_MAP_JSON:?Set a JSON object mapping tenant_id to the approved site-review attendee resource names}"
COUNTERSIGN_CFI_SITE_REVIEW_DELAY_HOURS="${COUNTERSIGN_CFI_SITE_REVIEW_DELAY_HOURS:?Set the approved CFI site-review delay in hours}"
COUNTERSIGN_CFI_SITE_REVIEW_DURATION_MINUTES="${COUNTERSIGN_CFI_SITE_REVIEW_DURATION_MINUTES:?Set the approved CFI site-review duration in minutes}"
COUNTERSIGN_CFI_REPORTS_FOLDER_MAP_JSON="${COUNTERSIGN_CFI_REPORTS_FOLDER_MAP_JSON:?Set a JSON object mapping tenant_id to the dedicated CFI report Drive folder id}"

# Calendar attendee invitations and CFI Docs/Drive report creation cannot use
# service-account-owned storage. They use Workspace Domain-Wide Delegation.
# COUNTERSIGN_DWD_* are the preferred shared names; the Calendar aliases are
# retained for compatibility with the first Phase 3 deployment. The subject
# must be a real Workspace user with access to every configured Calendar and
# report folder. The signer defaults to ${MCP_SERVICE_ACCOUNT} itself, which
# must hold roles/iam.serviceAccountTokenCreator on itself for keyless JWT
# delegation.
COUNTERSIGN_DWD_SUBJECT="${COUNTERSIGN_DWD_SUBJECT:-${COUNTERSIGN_CALENDAR_DWD_SUBJECT:-}}"
COUNTERSIGN_DWD_SIGNER="${COUNTERSIGN_DWD_SIGNER:-${COUNTERSIGN_CALENDAR_DWD_SIGNER:-}}"
COUNTERSIGN_CALENDAR_DWD_SUBJECT="${COUNTERSIGN_CALENDAR_DWD_SUBJECT:-}"
COUNTERSIGN_CALENDAR_DWD_SIGNER="${COUNTERSIGN_CALENDAR_DWD_SIGNER:-}"

# Item 5 (SENAITE LIMS). Only required when COUNTERSIGN_LIMS_BACKEND=senaite.
# The password is never passed as a plain --set-env-vars value; it is bound
# from Secret Manager via --set-secrets below, read only inside this process.
COUNTERSIGN_LIMS_BACKEND="${COUNTERSIGN_LIMS_BACKEND:-}"
COUNTERSIGN_SENAITE_URL="${COUNTERSIGN_SENAITE_URL:-}"
COUNTERSIGN_SENAITE_USERNAME="${COUNTERSIGN_SENAITE_USERNAME:-}"

echo "Building ${IMAGE} from Dockerfile.mcp"
gcloud builds submit "${ROOT}" \
  --project="${PROJECT_ID}" \
  --config="${ROOT}/infra/geap/cloudbuild-department-mcp.yaml" \
  --substitutions="_IMAGE=${IMAGE}"

echo "Deploying ${SERVICE} with --no-allow-unauthenticated"
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${LOCATION}" \
  --image="${IMAGE}" \
  --port=8080 \
  --no-allow-unauthenticated \
  --service-account="${MCP_SERVICE_ACCOUNT}" \
  --set-env-vars="^;^INSTANCE_CONNECTION_NAME=${SQL_INSTANCE_CONNECTION_NAME};DB_NAME=${SESSIONS_SQL_DB};DB_IAM_USER=${SQL_DB_IAM_USER};GOOGLE_CLOUD_PROJECT=${PROJECT_ID};GOOGLE_GENAI_USE_VERTEXAI=true;COUNTERSIGN_CASE_MEMORY_DATABASE=${CASE_MEMORY_DATABASE:-(default)};COUNTERSIGN_CASE_MEMORY_COLLECTION=${CASE_MEMORY_COLLECTION:-case_memory_facts};COUNTERSIGN_CASE_MEMORY_BACKEND=${COUNTERSIGN_CASE_MEMORY_BACKEND:-};COUNTERSIGN_CASE_MEMORY_BQ_DATASET=${COUNTERSIGN_CASE_MEMORY_BQ_DATASET:-countersign_case_memory};COUNTERSIGN_CASE_MEMORY_BQ_TABLE=${COUNTERSIGN_CASE_MEMORY_BQ_TABLE:-case_memory_facts};COUNTERSIGN_LIMS_BACKEND=${COUNTERSIGN_LIMS_BACKEND:-};COUNTERSIGN_LIMS_DB=${COUNTERSIGN_LIMS_DB:-countersign_lims};COUNTERSIGN_LIMS_TABLE=${COUNTERSIGN_LIMS_TABLE:-lims_results};COUNTERSIGN_EM_BACKEND=${COUNTERSIGN_EM_BACKEND:-};COUNTERSIGN_EM_DATASET=${COUNTERSIGN_EM_DATASET:-countersign_em};COUNTERSIGN_EM_TABLE=${COUNTERSIGN_EM_TABLE:-em_readings};COUNTERSIGN_SIBLING_BATCH_BACKEND=${COUNTERSIGN_SIBLING_BATCH_BACKEND:-};COUNTERSIGN_SIBLING_BATCH_TABLE=${COUNTERSIGN_SIBLING_BATCH_TABLE:-batch_material_lots};COUNTERSIGN_SPONSOR_TENANT_MAP=${COUNTERSIGN_SPONSOR_TENANT_MAP:-};COUNTERSIGN_QMS_REPORTS_FOLDER_ID=${COUNTERSIGN_QMS_REPORTS_FOLDER_ID:-};COUNTERSIGN_CFI_CHAT_SPACE_MAP_JSON=${COUNTERSIGN_CFI_CHAT_SPACE_MAP_JSON};COUNTERSIGN_CFI_CHAT_RESPONDER_MAP_JSON=${COUNTERSIGN_CFI_CHAT_RESPONDER_MAP_JSON};COUNTERSIGN_CFI_CHAT_MAX_PAGES=${COUNTERSIGN_CFI_CHAT_MAX_PAGES};COUNTERSIGN_CFI_INTERVIEW_DEADLINE_SECONDS=${COUNTERSIGN_CFI_INTERVIEW_DEADLINE_SECONDS};COUNTERSIGN_CFI_MAX_NUDGES=${COUNTERSIGN_CFI_MAX_NUDGES};COUNTERSIGN_CFI_NUDGE_INTERVAL_SECONDS=${COUNTERSIGN_CFI_NUDGE_INTERVAL_SECONDS};COUNTERSIGN_CFI_TICK_CALLER_SERVICE_ACCOUNTS=${COUNTERSIGN_CFI_TICK_CALLER_SERVICE_ACCOUNTS};COUNTERSIGN_CFI_CALENDAR_MAP_JSON=${COUNTERSIGN_CFI_CALENDAR_MAP_JSON};COUNTERSIGN_CFI_CALENDAR_ATTENDEE_MAP_JSON=${COUNTERSIGN_CFI_CALENDAR_ATTENDEE_MAP_JSON};COUNTERSIGN_CFI_SITE_REVIEW_DELAY_HOURS=${COUNTERSIGN_CFI_SITE_REVIEW_DELAY_HOURS};COUNTERSIGN_CFI_SITE_REVIEW_DURATION_MINUTES=${COUNTERSIGN_CFI_SITE_REVIEW_DURATION_MINUTES};COUNTERSIGN_CFI_REPORTS_FOLDER_MAP_JSON=${COUNTERSIGN_CFI_REPORTS_FOLDER_MAP_JSON};COUNTERSIGN_DWD_SUBJECT=${COUNTERSIGN_DWD_SUBJECT};COUNTERSIGN_DWD_SIGNER=${COUNTERSIGN_DWD_SIGNER};COUNTERSIGN_CALENDAR_DWD_SUBJECT=${COUNTERSIGN_CALENDAR_DWD_SUBJECT};COUNTERSIGN_CALENDAR_DWD_SIGNER=${COUNTERSIGN_CALENDAR_DWD_SIGNER};COUNTERSIGN_LIMS_BACKEND=${COUNTERSIGN_LIMS_BACKEND};COUNTERSIGN_SENAITE_URL=${COUNTERSIGN_SENAITE_URL};COUNTERSIGN_SENAITE_USERNAME=${COUNTERSIGN_SENAITE_USERNAME}" \
  --set-secrets="COUNTERSIGN_SENAITE_PASSWORD=countersign-senaite-admin-password:latest" \
  --min-instances=0 \
  --max-instances=3 \
  --quiet

echo "Granting Case Memory IAM (Firestore + Vertex embeddings)"
for role in roles/datastore.user roles/aiplatform.user; do
  granted=0
  for attempt in 1 2 3 4 5; do
    if gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${MCP_SERVICE_ACCOUNT}" \
      --role="${role}" \
      --condition=None \
      --quiet; then
      granted=1
      break
    fi
    echo "IAM bind ${role} conflicted, retry ${attempt}"
    sleep $((attempt * 2))
  done
  if [[ "${granted}" -ne 1 ]]; then
    echo "Failed to grant ${role}" >&2
    exit 1
  fi
done

echo "Deployed. Register this URL in Agent Registry and grant roles/iap.egressor"
echo "to the case Workflow Agent Identity. Do not bind the qc_probe tool-name"
echo "policy to that identity."
echo "CFI Chat interviews (chat_send_question/chat_read_replies) require"
echo "chat.googleapis.com enabled on ${PROJECT_ID}, a configured Google Chat"
echo "app, and ${MCP_SERVICE_ACCOUNT} added as a member with post/read access"
echo "on every Chat space named in COUNTERSIGN_CFI_CHAT_SPACE_MAP_JSON. Chat"
echo "app configuration is Console-only, like the OAuth consent screen."
echo "COUNTERSIGN_CFI_TICK_CALLER_SERVICE_ACCOUNTS must match the MCP"
echo "OIDC invoker identity configured on the tick service exactly, or"
echo "cfi_interview_tick fails closed for every scheduler call."
echo "CFI Phase 3 requires tenant-locked Calendar policy in"
echo "COUNTERSIGN_CFI_CALENDAR_MAP_JSON and"
echo "COUNTERSIGN_CFI_CALENDAR_ATTENDEE_MAP_JSON, plus the approved delay"
echo "and duration values; the MCP derives the event window server-side."
echo "COUNTERSIGN_CFI_REPORTS_FOLDER_MAP_JSON must map every tenant to its"
echo "dedicated CFI report folder, and ${MCP_SERVICE_ACCOUNT} needs Docs/Drive"
echo "Editor access there. CFI report filing is separate from qms_report_write."
echo "COUNTERSIGN_QMS_REPORTS_FOLDER_ID should be the '03 Deviation Reports' folder"
echo "under the shared Countersign QMS tree; if it is unset, qms_report_write fails closed."
echo "When set, ${MCP_SERVICE_ACCOUNT} must hold Editor (not just reader)"
echo "on that folder, or qms_report_write fails closed."
echo "Firestore Native database and vector indexes are provisioned by"
echo "infra/geap/provision_case_memory.sh (tenant_id + redacted pre-filter"
echo "on the embedding index, plus the recency fallback index)."
echo "COUNTERSIGN_CASE_MEMORY_BACKEND=bigquery switches Case Memory onto real"
echo "BigQuery VECTOR_SEARCH (Doc 12 T1-4) instead of Firestore. Provision"
echo "and grant its own IAM with infra/geap/provision_case_memory_bigquery.sh"
echo "before setting this; the Firestore roles granted above are unused by"
echo "that backend but harmless to also hold."
