#!/usr/bin/env bash
# Provision the GCE resources used by the Countersign ERPNext stack.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEPLOY_DIR="${ROOT}/infra/geap/erpnext"

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
ACME_EMAIL="${ACME_EMAIL:?Set ACME_EMAIL for ACME notices}"
# If ERPNEXT_DOMAIN is set, use it. Otherwise, if ERPNEXT_USE_NIP_IO=1,
# derive the domain from the reserved IP after it is created (<IP>.nip.io).
DOMAIN="${ERPNEXT_DOMAIN:-}"
USE_NIP_IO="${ERPNEXT_USE_NIP_IO:-0}"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-us-central1-a}"
REPOSITORY="${ARTIFACT_REPOSITORY:-countersign}"
IMAGE_NAME="countersign-erpnext"
IMAGE_TAG="v16.31.1-countersign-1"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${IMAGE_TAG}"
VM_NAME="${ERPNEXT_VM_NAME:-countersign-erpnext}"
SERVICE_ACCOUNT_NAME="${ERPNEXT_SERVICE_ACCOUNT_NAME:-countersign-erpnext-vm}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
NETWORK="${ERPNEXT_NETWORK:-countersign-erpnext}"
SUBNET="${ERPNEXT_SUBNET:-countersign-erpnext-${REGION}}"
ADDRESS_NAME="${ERPNEXT_ADDRESS_NAME:-countersign-erpnext-ip}"
FIREWALL_NAME="${ERPNEXT_FIREWALL_NAME:-countersign-erpnext-https}"
BACKUP_BUCKET="${ERPNEXT_BACKUP_BUCKET:-${PROJECT_ID}-countersign-erpnext-backups}"
BACKUP_RETENTION="${ERPNEXT_BACKUP_RETENTION:-30d}"
DB_SECRET="${ERPNEXT_DB_SECRET:-countersign-erpnext-db-password}"
ADMIN_SECRET="${ERPNEXT_ADMIN_SECRET:-countersign-erpnext-admin-password}"
BACKUP_SECRET="${ERPNEXT_BACKUP_SECRET:-countersign-erpnext-backup-password}"
API_CREDENTIALS_SECRET="${ERPNEXT_API_CREDENTIALS_SECRET:-countersign-erpnext-credentials}"
DEPARTMENT_MCP_SA="${DEPARTMENT_MCP_SERVICE_ACCOUNT:-countersign-department-mcp@${PROJECT_ID}.iam.gserviceaccount.com}"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Required command not found: $1" >&2
    exit 1
  }
}

ensure_secret() {
  local name="$1"
  local bytes="$2"
  if ! gcloud secrets describe "${name}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    openssl rand -hex "${bytes}" \
      | gcloud secrets create "${name}" \
          --project="${PROJECT_ID}" \
          --replication-policy=automatic \
          --data-file=- >/dev/null
  fi
}

bind_secret() {
  gcloud secrets add-iam-policy-binding "$1" \
    --project="${PROJECT_ID}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role=roles/secretmanager.secretAccessor \
    --condition=None >/dev/null
}

main() {
  require_command gcloud
  require_command openssl
  require_command getent

  if [[ -z "${DOMAIN}" && "${USE_NIP_IO}" != "1" ]]; then
    echo "Set either ERPNEXT_DOMAIN or ERPNEXT_USE_NIP_IO=1." >&2
    exit 1
  fi
  if [[ "${ZONE}" != "${REGION}-"* ]]; then
    echo "ZONE must belong to REGION." >&2
    exit 1
  fi

  gcloud artifacts repositories describe "${REPOSITORY}" \
    --project="${PROJECT_ID}" --location="${REGION}" >/dev/null || {
      echo "Artifact Registry repository ${REPOSITORY} must already exist in ${REGION}." >&2
      exit 1
    }

  if ! gcloud compute addresses describe "${ADDRESS_NAME}" \
    --project="${PROJECT_ID}" --region="${REGION}" >/dev/null 2>&1; then
    gcloud compute addresses create "${ADDRESS_NAME}" \
      --project="${PROJECT_ID}" --region="${REGION}" --network-tier=PREMIUM
  fi
  local reserved_ip
  reserved_ip="$(gcloud compute addresses describe "${ADDRESS_NAME}" \
    --project="${PROJECT_ID}" --region="${REGION}" --format='value(address)')"

  if [[ "${USE_NIP_IO}" == "1" && -z "${DOMAIN}" ]]; then
    DOMAIN="${reserved_ip}.nip.io"
    echo "Using nip.io domain: ${DOMAIN}"
  fi

  if [[ "${USE_NIP_IO}" != "1" ]]; then
    if ! getent ahostsv4 "${DOMAIN}" | awk '{print $1}' | grep -Fxq "${reserved_ip}"; then
      echo "DNS prerequisite not met: point ${DOMAIN} A records to ${reserved_ip}, then rerun." >&2
      exit 2
    fi
  fi

  if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${SERVICE_ACCOUNT_NAME}" \
      --project="${PROJECT_ID}" --display-name="Countersign ERPNext VM"
  fi

  gcloud artifacts repositories add-iam-policy-binding "${REPOSITORY}" \
    --project="${PROJECT_ID}" --location="${REGION}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role=roles/artifactregistry.reader --condition=None >/dev/null
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role=roles/logging.logWriter --condition=None >/dev/null

  ensure_secret "${DB_SECRET}" 32
  ensure_secret "${ADMIN_SECRET}" 24
  ensure_secret "${BACKUP_SECRET}" 32
  bind_secret "${DB_SECRET}"
  bind_secret "${ADMIN_SECRET}"
  bind_secret "${BACKUP_SECRET}"

  # The ERPNext API credentials secret is populated by post_setup.sh after
  # the API user is created and keys are generated. Create the empty secret
  # now so bind_secret can grant the department MCP access before the VM
  # is up — post_setup.sh updates it in place.
  if ! gcloud secrets describe "${API_CREDENTIALS_SECRET}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    printf '{}' | gcloud secrets create "${API_CREDENTIALS_SECRET}" \
      --project="${PROJECT_ID}" \
      --replication-policy=automatic \
      --data-file=- >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "${API_CREDENTIALS_SECRET}" \
    --project="${PROJECT_ID}" \
    --member="serviceAccount:${DEPARTMENT_MCP_SA}" \
    --role=roles/secretmanager.secretAccessor \
    --condition=None >/dev/null

  if ! gcloud storage buckets describe "gs://${BACKUP_BUCKET}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${BACKUP_BUCKET}" \
      --project="${PROJECT_ID}" \
      --location="${REGION}" \
      --uniform-bucket-level-access \
      --public-access-prevention
  fi
  gcloud storage buckets update "gs://${BACKUP_BUCKET}" \
    --project="${PROJECT_ID}" \
    --uniform-bucket-level-access \
    --pap \
    --versioning \
    --retention-period="${BACKUP_RETENTION}" >/dev/null
  gcloud storage buckets add-iam-policy-binding "gs://${BACKUP_BUCKET}" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role=roles/storage.objectAdmin >/dev/null

  if ! gcloud compute networks describe "${NETWORK}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud compute networks create "${NETWORK}" \
      --project="${PROJECT_ID}" --subnet-mode=custom
  fi
  if ! gcloud compute networks subnets describe "${SUBNET}" \
    --project="${PROJECT_ID}" --region="${REGION}" >/dev/null 2>&1; then
    gcloud compute networks subnets create "${SUBNET}" \
      --project="${PROJECT_ID}" --region="${REGION}" \
      --network="${NETWORK}" --range="10.82.0.0/24" \
      --enable-private-ip-google-access
  fi
  if ! gcloud compute firewall-rules describe "${FIREWALL_NAME}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud compute firewall-rules create "${FIREWALL_NAME}" \
      --project="${PROJECT_ID}" --network="${NETWORK}" \
      --direction=INGRESS --priority=1000 --action=ALLOW \
      --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 \
      --target-service-accounts="${SERVICE_ACCOUNT}"
  fi

  gcloud builds submit "${DEPLOY_DIR}" \
    --project="${PROJECT_ID}" \
    --config="${DEPLOY_DIR}/cloudbuild.yaml" \
    --substitutions="_LOCATION=${REGION},_REPOSITORY=${REPOSITORY},_IMAGE=${IMAGE_NAME},_TAG=${IMAGE_TAG}"

  local metadata
  metadata="erpnext-domain=${DOMAIN},erpnext-image=${IMAGE_URI},erpnext-backup-bucket=${BACKUP_BUCKET},erpnext-acme-email=${ACME_EMAIL},erpnext-db-secret=${DB_SECRET},erpnext-admin-secret=${ADMIN_SECRET},erpnext-backup-secret=${BACKUP_SECRET},block-project-ssh-keys=true,enable-oslogin=true"
  if gcloud compute instances describe "${VM_NAME}" \
    --project="${PROJECT_ID}" --zone="${ZONE}" >/dev/null 2>&1; then
    gcloud compute instances add-metadata "${VM_NAME}" \
      --project="${PROJECT_ID}" --zone="${ZONE}" \
      --metadata="${metadata}" \
      --metadata-from-file="startup-script=${DEPLOY_DIR}/startup.sh,erpnext-compose=${DEPLOY_DIR}/compose.yaml"
    gcloud compute instances reset "${VM_NAME}" \
      --project="${PROJECT_ID}" --zone="${ZONE}"
  else
    gcloud compute instances create "${VM_NAME}" \
      --project="${PROJECT_ID}" --zone="${ZONE}" \
      --machine-type="${ERPNEXT_MACHINE_TYPE:-e2-standard-2}" \
      --subnet="${SUBNET}" --address="${reserved_ip}" --network-tier=PREMIUM \
      --service-account="${SERVICE_ACCOUNT}" --scopes=cloud-platform \
      --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
      --boot-disk-size="${ERPNEXT_BOOT_DISK_SIZE:-50GB}" \
      --boot-disk-type=pd-balanced --shielded-secure-boot \
      --shielded-vtpm --shielded-integrity-monitoring \
      --no-can-ip-forward \
      --metadata="${metadata}" \
      --metadata-from-file="startup-script=${DEPLOY_DIR}/startup.sh,erpnext-compose=${DEPLOY_DIR}/compose.yaml"
  fi

  echo "Provisioning submitted. Verify at https://${DOMAIN} after startup completes."
}

main "$@"
