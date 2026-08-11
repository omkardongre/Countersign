#!/usr/bin/env bash
# Step 4 acceptance gate: prove the GEAP governance chain end to end.
#
# Fails unless ALL of the following hold:
#   1. effectiveIdentity is an org-scoped Agent Identity (not a service account)
#   2. the Runtime instance is bound to the Agent Gateway
#   3. the authorization extension is in enforcement mode (not DRY_RUN)
#   4. a registered destination is reachable      -> ALLOWED
#   5. an unregistered destination is blocked     -> 403 at the tool boundary
#   6. Gateway request logs contain both ALLOWED and DENIED rows
#   7. IAP logged granted=false on iap.webServiceVersions.egressViaIAP
#
# Writes machine-readable evidence to infra/geap/evidence/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"

EVIDENCE_DIR="${ROOT}/infra/geap/evidence"
mkdir -p "${EVIDENCE_DIR}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
UNREGISTERED_URL="${UNREGISTERED_URL:-https://httpbin.org/get}"

META="${ROOT}/deployment_metadata.json"
RESOURCE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["remote_agent_runtime_id"])' "${META}")"
TOKEN="$(gcloud auth print-access-token)"
STARTED_AT="$(date -u +%FT%TZ)"

fail() { echo "FAIL: $*" >&2; exit 1; }

# ---------------------------------------------------------------- 1 + 2
echo "=== 1/7 + 2/7  identity and gateway binding ==="
curl -sS -H "Authorization: Bearer ${TOKEN}" \
  "https://${LOCATION}-aiplatform.googleapis.com/v1beta1/${RESOURCE}" \
  -o "${EVIDENCE_DIR}/reasoning_engine.json"

python3 - "${EVIDENCE_DIR}/reasoning_engine.json" "${ORG_ID}" <<'PY'
import json, sys
spec = json.load(open(sys.argv[1]))["spec"]
org = sys.argv[2]
identity = spec.get("effectiveIdentity") or ""
gateway = ((spec.get("deploymentSpec") or {}).get("agentGatewayConfig") or {})
print("effectiveIdentity:", identity)
if f"agents.global.org-{org}" not in identity:
    raise SystemExit(f"FAIL: effectiveIdentity is not an org-scoped Agent Identity: {identity!r}")
if "@" in identity:
    raise SystemExit(f"FAIL: effectiveIdentity fell back to a service account: {identity!r}")
bound = gateway.get("agentToAnywhereConfig", {}).get("agentGateway")
if not bound:
    raise SystemExit("FAIL: no agentGatewayConfig on the Runtime instance")
print("agentGateway:", bound)
print("PASS identity + gateway binding")
PY

# ---------------------------------------------------------------- 3
echo
echo "=== 3/7  authorization extension is enforcing ==="
curl -sS -H "Authorization: Bearer ${TOKEN}" \
  "https://networkservices.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/${LOCATION}/authzExtensions" \
  -o "${EVIDENCE_DIR}/authz_extensions.json"

python3 - "${EVIDENCE_DIR}/authz_extensions.json" <<'PY'
import json, sys
exts = json.load(open(sys.argv[1])).get("authzExtensions") or []
if not exts:
    raise SystemExit("FAIL: no authorization extension attached to the gateway")
for e in exts:
    mode = (e.get("metadata") or {}).get("iamEnforcementMode", "ENFORCED")
    print(f"{e['name'].split('/')[-1]}: iamEnforcementMode={mode} failOpen={e.get('failOpen', False)}")
    if mode == "DRY_RUN":
        raise SystemExit("FAIL: extension is in DRY_RUN — denies are logged but not enforced")
    if e.get("failOpen"):
        raise SystemExit("FAIL: failOpen=true — the gateway would admit traffic if authz is unavailable")
print("PASS enforcement mode")
PY

# ---------------------------------------------------------------- 4
echo
echo "=== 4/7  registered destination is reachable (ALLOWED path) ==="
curl -sS --max-time 240 -X POST \
  -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  "https://${LOCATION}-aiplatform.googleapis.com/v1beta1/${RESOURCE}:streamQuery?alt=sse" \
  -d '{"class_method":"stream_query","input":{"user_id":"gov-verify-allow","message":"What is the weather in San Francisco? Reply briefly."}}' \
  > "${EVIDENCE_DIR}/stream_allowed.sse"

grep -q '"finish_reason": "STOP"' "${EVIDENCE_DIR}/stream_allowed.sse" \
  || fail "allowed path did not complete — the model call itself may be blocked"
echo "PASS model egress reached Vertex through the gateway"

# ---------------------------------------------------------------- 5
echo
echo "=== 5/7  unregistered destination is blocked (DENIED path) ==="
curl -sS --max-time 240 -X POST \
  -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  "https://${LOCATION}-aiplatform.googleapis.com/v1beta1/${RESOURCE}:streamQuery?alt=sse" \
  -d "{\"class_method\":\"stream_query\",\"input\":{\"user_id\":\"gov-verify-deny\",\"message\":\"Call probe_http_get exactly once with url='${UNREGISTERED_URL}' and return only the tool result.\"}}" \
  > "${EVIDENCE_DIR}/stream_denied.sse"

python3 - "${EVIDENCE_DIR}/stream_denied.sse" <<'PY'
import json, sys
observed = []
for line in open(sys.argv[1]):
    line = line.strip()
    if not line:
        continue
    try:
        event = json.loads(line)
    except ValueError:
        continue
    for part in (event.get("content") or {}).get("parts") or []:
        if "functionResponse" in part:
            observed.append(json.dumps(part["functionResponse"].get("response")))
        elif part.get("text"):
            observed.append(part["text"])
blob = " ".join(observed)
print("tool/model output:", blob.strip()[:300])
if "403" not in blob:
    raise SystemExit("FAIL: unregistered egress was not blocked with 403")
print("PASS unregistered egress blocked at the tool boundary")
PY

# ---------------------------------------------------------------- 6
echo
echo "=== 6/7  Gateway request logs contain ALLOWED and DENIED ==="
sleep 20
gcloud logging read \
  'resource.type="networkservices.googleapis.com/Gateway" AND httpRequest.requestUrl:*' \
  --project="${PROJECT_ID}" --freshness=30m --limit=300 \
  --format='value(httpRequest.status,jsonPayload.authzPolicyInfo.result,httpRequest.requestUrl)' \
  | sort -u > "${EVIDENCE_DIR}/gateway_requests.tsv"

python3 - "${EVIDENCE_DIR}/gateway_requests.tsv" "${UNREGISTERED_URL}" <<'PY'
import sys
rows = [r for r in open(sys.argv[1]).read().splitlines() if r.strip()]
target = sys.argv[2]
allowed = [r for r in rows if "\tALLOWED\t" in r]
denied = [r for r in rows if "\tDENIED\t" in r]
print(f"rows={len(rows)} allowed={len(allowed)} denied={len(denied)}")
for r in denied:
    print("  DENY:", r)
if not allowed:
    raise SystemExit("FAIL: no ALLOWED rows — gateway may not be on the traffic path")
if not any(target in r for r in denied):
    raise SystemExit(f"FAIL: no DENIED row for {target}")
print("PASS gateway logged both decisions")
PY

# ---------------------------------------------------------------- 7
echo
echo "=== 7/7  IAP recorded an egress denial ==="
gcloud logging read \
  'labels."iap.googleapis.com/audited_resource_name":"unregisteredEndpoint"' \
  --project="${PROJECT_ID}" --freshness=30m --limit=50 \
  --format='value(timestamp,protoPayload.authorizationInfo[0].granted,protoPayload.authorizationInfo[0].permission)' \
  > "${EVIDENCE_DIR}/iap_egress_decisions.tsv"

python3 - "${EVIDENCE_DIR}/iap_egress_decisions.tsv" <<'PY'
import sys
rows = [r.split("\t") for r in open(sys.argv[1]).read().splitlines() if r.strip()]
denied = [r for r in rows if len(r) > 1 and r[1] == "False"]
print(f"iap decisions={len(rows)} denied={len(denied)}")
for r in denied[:5]:
    print("  ", "\t".join(r))
if not denied:
    raise SystemExit("FAIL: IAP never recorded granted=false on egressViaIAP")
print("PASS IAP denial recorded on iap.webServiceVersions.egressViaIAP")
PY

# ---------------------------------------------------------------- summary
python3 - "${EVIDENCE_DIR}/summary.json" "${RUN_ID}" "${STARTED_AT}" "${RESOURCE}" "${UNREGISTERED_URL}" <<'PY'
import json, sys
out, run_id, started, resource, target = sys.argv[1:6]
json.dump({
    "run_id": run_id,
    "started_at": started,
    "runtime_resource": resource,
    "unregistered_target": target,
    "checks": [
        "agent_identity_org_scoped",
        "runtime_bound_to_gateway",
        "authz_extension_enforcing",
        "registered_destination_allowed",
        "unregistered_destination_denied_403",
        "gateway_logged_allowed_and_denied",
        "iap_logged_egress_denial",
    ],
    "result": "PASS",
}, open(out, "w"), indent=2)
PY

echo
echo "=== ALL 7 CHECKS PASSED ==="
echo "Evidence: ${EVIDENCE_DIR}"
