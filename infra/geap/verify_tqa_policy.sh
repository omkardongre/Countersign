#!/usr/bin/env bash
# Prove TQA allow + tenant deny + lifecycle deny against the callout.
# Then confirm gateway networkConfig and failOpen=false. Does not invoke Runtime.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
_TQA_ZONE_OVERRIDE="${TQA_ZONE:-}"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

ZONE="${_TQA_ZONE_OVERRIDE:-${TQA_ZONE:-${LOCATION}-c}}"
EVIDENCE="${ROOT}/infra/geap/evidence"
mkdir -p "${EVIDENCE}"

gcloud config set account admin@countersign.site --quiet
gcloud config set project "${PROJECT_ID}" --quiet

IMAGE="$(
  gcloud compute ssh "${TQA_CALLOUT_INSTANCE}" \
    --project="${PROJECT_ID}" --zone="${ZONE}" --tunnel-through-iap --quiet \
    --command="sudo docker inspect -f '{{.Config.Image}}' tqa-callout"
)"

gcloud compute ssh "${TQA_CALLOUT_INSTANCE}" \
  --project="${PROJECT_ID}" --zone="${ZONE}" --tunnel-through-iap --quiet \
  --command="sudo docker run --rm --network host --entrypoint /code/.venv/bin/python ${IMAGE} -m countersign_tqa.probe ${TQA_FQDN}" \
  | tee "${EVIDENCE}/tqa-policy.txt"

grep -q "PASS:" "${EVIDENCE}/tqa-policy.txt"

gcloud network-services agent-gateways describe "${AGENT_GATEWAY}" \
  --project="${PROJECT_ID}" --location="${LOCATION}" \
  --format=json > "${EVIDENCE}/tqa-gateway.json"

python3 - "${EVIDENCE}/tqa-gateway.json" "${TQA_NETWORK_ATTACHMENT}" <<'PY'
import json, sys
gw = json.load(open(sys.argv[1]))
attachment = sys.argv[2]
cfg = ((gw.get("networkConfig") or {}).get("egress") or {})
got = cfg.get("networkAttachment", "")
if attachment not in got:
    raise SystemExit(f"FAIL: networkAttachment missing: {got!r}")
domains = ((gw.get("networkConfig") or {}).get("dnsPeeringConfig") or {}).get("domains") or []
if "countersign.internal." not in domains:
    raise SystemExit(f"FAIL: dns peering domain missing: {domains!r}")
print("PASS: gateway networkConfig")
PY

gcloud service-extensions authz-extensions describe countersign-tqa \
  --project="${PROJECT_ID}" --location="${LOCATION}" --format=json \
  > "${EVIDENCE}/tqa-extension.json"

python3 - "${EVIDENCE}/tqa-extension.json" <<'PY'
import json, sys
ext = json.load(open(sys.argv[1]))
if ext.get("failOpen") is True:
    raise SystemExit("FAIL: TQA extension failOpen must be false")
if ext.get("service") != "tqa.countersign.internal":
    raise SystemExit(f"FAIL: unexpected service {ext.get('service')!r}")
print("PASS: TQA extension failOpen=false")
PY

echo "PASS: TQA policy + gateway VPC attach"
echo "Runtime MCP 403 through the gateway is a separate proof (case Workflow on Runtime)."
