#!/usr/bin/env bash
# Provision Doc 12 T1-5 controlled-guidance RAG: two Vertex AI RAG Engine
# corpora (controlled SOP, public guidance), IAM for the department MCP
# service account, and the env vars that switch retrieval on.
#
# This does not import files. Corpus creation only creates the empty index;
# importing the tenant's approved SOPs from Drive and the public Annex 1 /
# ICH Q9(R1) / OOS PDFs from Cloud Storage is a deliberate, reviewed step
# (see the echoed import commands at the end), not something this script
# does unattended — a controlled-document corpus must not be populated by
# an unattended script run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/constants.env"

MCP_SA="${MCP_SERVICE_ACCOUNT:-countersign-department-mcp@${PROJECT_ID}.iam.gserviceaccount.com}"
SOP_CORPUS_DISPLAY_NAME="${GUIDANCE_SOP_CORPUS_NAME:-countersign-controlled-sop}"
PUBLIC_CORPUS_DISPLAY_NAME="${GUIDANCE_PUBLIC_CORPUS_NAME:-countersign-public-guidance}"

gcloud config set account admin@countersign.site >/dev/null
gcloud config set project "${PROJECT_ID}" >/dev/null

gcloud services enable \
  aiplatform.googleapis.com \
  --project="${PROJECT_ID}"

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

# aiplatform.user is the same role Case Memory's BigQuery backend already
# grants for Vertex embedding calls; RAG Engine corpus/retrieval calls are
# under the same Vertex AI IAM surface.
echo "Granting roles/aiplatform.user to ${MCP_SA}"
grant_role "serviceAccount:${MCP_SA}" "roles/aiplatform.user"

echo "--- Create the two RAG corpora (run once; safe to re-run, idempotent lookup below) ---"
cat <<'PYEOF'
python3 - <<'PY'
import os
from vertexai import rag
import vertexai

vertexai.init(project=os.environ["PROJECT_ID"], location=os.environ.get("LOCATION", "us-central1"))

for name in (
    os.environ.get("GUIDANCE_SOP_CORPUS_NAME", "countersign-controlled-sop"),
    os.environ.get("GUIDANCE_PUBLIC_CORPUS_NAME", "countersign-public-guidance"),
):
    corpus = rag.create_corpus(display_name=name)
    print(name, "->", corpus.name)
PY
PYEOF
echo
echo "Copy each printed corpus resource name into:"
echo "  COUNTERSIGN_GUIDANCE_SOP_CORPUS=<controlled-sop corpus name>"
echo "  COUNTERSIGN_GUIDANCE_PUBLIC_CORPUS=<public-guidance corpus name>"
echo "on the department MCP service (deploy_department_mcp.sh's --set-env-vars)."
echo
echo "--- Import files (run manually, after corpus creation, per corpus) ---"
echo "The Agent Platform RAG Data Service Agent (Google-provided role grant)"
echo "must have Viewer permission on the Drive folder or Cloud Storage bucket"
echo "before an import can succeed — see"
echo "docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/use-data-ingestion."
echo
echo "Controlled SOP corpus, imported from the tenant's own approved SOP"
echo "Drive folder, with tenant_id attached as file metadata so the CEL"
echo "metadata_filter in app/domain/guidance_source.py can pre-filter by"
echo "tenant at retrieval time (see docs.cloud.google.com/vertex-ai/"
echo "generative-ai/docs/rag-engine/use-metadata-search):"
echo "  rag.import_files(corpus_name=SOP_CORPUS, paths=['https://drive.google.com/drive/folders/<SOP_FOLDER_ID>'])"
echo "  # then batch-attach {'tenant_id': '<tenant>'} metadata per imported file"
echo
echo "Public-guidance corpus, imported from a Cloud Storage bucket holding"
echo "the Annex 1 / ICH Q9(R1) / OOS PDFs:"
echo "  rag.import_files(corpus_name=PUBLIC_CORPUS, paths=['gs://<bucket>/annex1.pdf', ...])"
echo
echo "After importing, add every imported file's exact source_uri, version,"
echo "approval, and effective_date to"
echo "app/domain/master_data/guidance_manifest_v1.json under change control —"
echo "a retrieved chunk with no matching manifest entry is dropped by"
echo "app/domain/guidance_source.py, never trusted on retrieval alone."
