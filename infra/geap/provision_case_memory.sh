#!/usr/bin/env bash
# Provision Firestore Native Case Memory: API, default database, IAM, indexes.
# Vector index includes tenant_id + redacted because query() pre-filters both
# before find_nearest (see app/domain/case_memory_store.py).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/constants.env"

COLLECTION="${CASE_MEMORY_COLLECTION:-case_memory_facts}"
DATABASE="${CASE_MEMORY_DATABASE:-"(default)"}"
MCP_SA="${MCP_SERVICE_ACCOUNT:-countersign-department-mcp@${PROJECT_ID}.iam.gserviceaccount.com}"

gcloud config set account admin@countersign.site >/dev/null
gcloud config set project "${PROJECT_ID}" >/dev/null

gcloud services enable \
  firestore.googleapis.com \
  aiplatform.googleapis.com \
  --project="${PROJECT_ID}"

db_exists=0
if gcloud firestore databases list \
  --project="${PROJECT_ID}" \
  --format='value(name)' \
  | grep -q "/databases/(default)$"; then
  db_exists=1
fi
if [[ "${db_exists}" -eq 1 ]]; then
  echo "Firestore database ${DATABASE} already exists"
else
  echo "Creating Firestore Native database ${DATABASE} in ${LOCATION}"
  set +e
  create_out="$(gcloud firestore databases create \
    --database="${DATABASE}" \
    --location="${LOCATION}" \
    --type=firestore-native \
    --project="${PROJECT_ID}" 2>&1)"
  create_status=$?
  set -e
  if [[ "${create_status}" -ne 0 ]]; then
    if grep -qiE 'already exists' <<<"${create_out}"; then
      echo "Firestore database ${DATABASE} already exists"
    else
      echo "${create_out}" >&2
      exit "${create_status}"
    fi
  fi
fi

grant_role() {
  local member="$1"
  local role="$2"
  local attempt
  for attempt in 1 2 3 4 5; do
    if gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="${member}" \
      --role="${role}" \
      --condition=None \
      --quiet >/dev/null; then
      return 0
    fi
    echo "IAM bind ${role} conflicted, retry ${attempt}"
    sleep $((attempt * 2))
  done
  echo "Failed to grant ${role} to ${member}" >&2
  return 1
}

for role in roles/datastore.user roles/aiplatform.user; do
  echo "Granting ${role} to ${MCP_SA}"
  grant_role "serviceAccount:${MCP_SA}" "${role}"
done

create_index() {
  local description="$1"
  shift
  local out
  set +e
  out="$(gcloud firestore indexes composite create \
    --project="${PROJECT_ID}" \
    --database="${DATABASE}" \
    --collection-group="${COLLECTION}" \
    --query-scope=COLLECTION \
    --async \
    "$@" 2>&1)"
  local status=$?
  set -e
  if [[ "${status}" -eq 0 ]]; then
    echo "Creating ${description}"
    return 0
  fi
  if grep -qiE 'ALREADY_EXISTS|already exists' <<<"${out}"; then
    echo "${description} already exists"
    return 0
  fi
  echo "${out}" >&2
  return "${status}"
}

# Pre-filter fields must sit on the same composite as the vector field.
create_index "vector index (tenant_id, redacted, embedding)" \
  --field-config=order=ASCENDING,field-path=tenant_id \
  --field-config=order=ASCENDING,field-path=redacted \
  --field-config=field-path=embedding,vector-config='{"dimension":"768","flat":"{}"}'

# Recency fallback: tenant_id ==, redacted ==, order_by observed_at DESC.
create_index "recency index (tenant_id, redacted, observed_at)" \
  --field-config=order=ASCENDING,field-path=tenant_id \
  --field-config=order=ASCENDING,field-path=redacted \
  --field-config=order=DESCENDING,field-path=observed_at

echo "Waiting for ${COLLECTION} composite indexes to become READY"
# Official list fields are name + state; collectionGroup is not a csv column
# (https://cloud.google.com/sdk/gcloud/reference/firestore/indexes/composite/list).
# Empty collection still takes a few minutes to reach READY
# (https://cloud.google.com/firestore/docs/query-data/indexing).
deadline=$((SECONDS + 900))
while (( SECONDS < deadline )); do
  states="$(gcloud firestore indexes composite list \
    --project="${PROJECT_ID}" \
    --database="${DATABASE}" \
    --format='csv[no-heading](name,state)' \
    | awk -F, -v needle="/collectionGroups/${COLLECTION}/" \
      'index($1, needle) { print $2 }')"
  if [[ -n "${states}" ]] && ! grep -qvE '^READY$' <<<"${states}"; then
    echo "All ${COLLECTION} indexes READY"
    echo "Case Memory Firestore ${DATABASE} / ${COLLECTION} ready."
    echo "Run: uv run python infra/geap/verify_case_memory.py"
    exit 0
  fi
  echo "Index states: ${states:-none yet}"
  sleep 15
done

echo "Timed out waiting for Firestore indexes on ${COLLECTION}" >&2
gcloud firestore indexes composite list \
  --project="${PROJECT_ID}" \
  --database="${DATABASE}"
exit 1
