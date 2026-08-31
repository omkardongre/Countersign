#!/usr/bin/env bash
# Provision BigQuery Case Memory (Doc 12 T1-4): dataset, table, vector index,
# IAM. Opt-in alternative to Firestore Case Memory
# (provision_case_memory.sh) — switch a deployment onto it by setting
# COUNTERSIGN_CASE_MEMORY_BACKEND=bigquery on the department MCP service
# (see deploy_department_mcp.sh).
#
# tenant_id and redacted are declared STORING columns on the vector index so
# the tenant/redaction pre-filter in
# BigQueryCaseMemoryStore.query (app/domain/case_memory_store_bigquery.py)
# can use the index rather than degrading to a full-table scan
# (cloud.google.com/bigquery/docs/vector-index#pre-filter-with-stored-columns).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/constants.env"

DATASET="${CASE_MEMORY_BQ_DATASET:-countersign_case_memory}"
TABLE="${CASE_MEMORY_BQ_TABLE:-case_memory_facts}"
MCP_SA="${MCP_SERVICE_ACCOUNT:-countersign-department-mcp@${PROJECT_ID}.iam.gserviceaccount.com}"

gcloud config set account admin@countersign.site >/dev/null
gcloud config set project "${PROJECT_ID}" >/dev/null

gcloud services enable \
  bigquery.googleapis.com \
  aiplatform.googleapis.com \
  --project="${PROJECT_ID}"

echo "--- Case Memory: BigQuery ${PROJECT_ID}:${DATASET}.${TABLE} ---"
bq --project_id="${PROJECT_ID}" mk --dataset \
  --location="${LOCATION}" \
  --description="Countersign Case Memory: prior-deviation facts (Doc 12 T1-4)" \
  "${PROJECT_ID}:${DATASET}" 2>/dev/null || true

# embedding must be non-NULL / uniform-dimension per row for the vector
# index (CREATE VECTOR INDEX requirement); rows are always written with a
# real embedding by BigQueryCaseMemoryStore.write_fact, which fails the
# write rather than inserting a NULL vector.
bq query --project_id="${PROJECT_ID}" --location="${LOCATION}" \
  --use_legacy_sql=false \
  "CREATE TABLE IF NOT EXISTS \`${PROJECT_ID}.${DATASET}.${TABLE}\` (
    fact_id STRING NOT NULL,
    tenant_id STRING NOT NULL,
    case_id STRING NOT NULL,
    batch_id STRING NOT NULL,
    fact STRING NOT NULL,
    source_uri STRING NOT NULL,
    confidence FLOAT64 NOT NULL,
    observed_at TIMESTAMP NOT NULL,
    redacted BOOL NOT NULL,
    redacted_reason STRING,
    root_cause_category STRING,
    embedding ARRAY<FLOAT64>
  )" >/dev/null

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

# Data Editor (read + write rows for write_fact/redact), Job User (run query
# jobs), aiplatform.user (Vertex embedding calls) — same three-role shape as
# the EM BigQuery source's dataViewer/jobUser plus the Case Memory
# Firestore store's aiplatform.user, since this store both reads and writes.
for role in roles/bigquery.dataEditor roles/bigquery.jobUser roles/aiplatform.user; do
  echo "Granting ${role} to ${MCP_SA}"
  grant_role "serviceAccount:${MCP_SA}" "${role}"
done

echo "--- Waiting for the table to hold rows before indexing ---"
echo "BigQuery does not populate a vector index on a table under 10 MB."
echo "Run a case (or the live probe) to write at least one fact, then create"
echo "the vector index:"
echo
echo "  bq query --project_id=${PROJECT_ID} --use_legacy_sql=false '"
echo "    CREATE VECTOR INDEX IF NOT EXISTS case_memory_facts_embedding_idx"
echo "    ON \`${PROJECT_ID}.${DATASET}.${TABLE}\`(embedding)"
echo "    STORING(tenant_id, redacted)"
echo "    OPTIONS(index_type = '\''IVF'\'', distance_type = '\''COSINE'\'')'"
echo
echo "Check readiness with:"
echo "  SELECT * FROM \`${PROJECT_ID}.${DATASET}.INFORMATION_SCHEMA.VECTOR_INDEXES\`;"
echo
echo "To switch the department MCP process onto this backend, add to"
echo "deploy_department_mcp.sh's --set-env-vars:"
echo "  COUNTERSIGN_CASE_MEMORY_BACKEND=bigquery,COUNTERSIGN_CASE_MEMORY_BQ_DATASET=${DATASET},COUNTERSIGN_CASE_MEMORY_BQ_TABLE=${TABLE}"
echo "Unset (or any other value) keeps Case Memory on Firestore."
