#!/usr/bin/env bash
# Verify identity, gateway binding, a live query, and Gateway audit rows.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/infra/geap/constants.env"
META="${ROOT}/deployment_metadata.json"
RESOURCE="$(python3 -c 'import json; print(json.load(open("'"${META}"'"))["remote_agent_runtime_id"])')"
TOKEN="$(gcloud auth print-access-token)"

echo "=== 1) identity + gateway ==="
curl -sS -H "Authorization: Bearer ${TOKEN}" \
  "https://${LOCATION}-aiplatform.googleapis.com/v1beta1/${RESOURCE}" \
  > /tmp/cs-re.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/cs-re.json"))
s=d["spec"]
identity=s.get("effectiveIdentity")
gw=(s.get("deploymentSpec") or {}).get("agentGatewayConfig")
print("effectiveIdentity:", identity)
print("agentGatewayConfig:", json.dumps(gw, indent=2))
assert identity and "agents.global.org-334195573957" in identity and "@" not in identity
assert gw and gw.get("agentToAnywhereConfig", {}).get("agentGateway")
print("PASS identity + gateway")
PY

echo "=== 2) streamQuery (weather — allowed path) ==="
curl -sS --max-time 180 -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  "https://${LOCATION}-aiplatform.googleapis.com/v1beta1/${RESOURCE}:streamQuery?alt=sse" \
  -d '{"class_method":"stream_query","input":{"user_id":"governance-verify","message":"What is the weather in San Francisco? Reply briefly."}}' \
  | tee /tmp/cs-stream-weather.txt | tail -c 2000
echo

echo "=== 3) streamQuery (unregistered egress probe) ==="
curl -sS --max-time 180 -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  "https://${LOCATION}-aiplatform.googleapis.com/v1beta1/${RESOURCE}:streamQuery?alt=sse" \
  -d '{"class_method":"stream_query","input":{"user_id":"governance-verify","message":"Call probe_http_get exactly once with url='"'"'https://httpbin.org/get'"'"' and return only the tool result."}}' \
  | tee /tmp/cs-stream-probe.txt | tail -c 2000
echo

echo "=== 4) Gateway audit log (official codelab filter) ==="
sleep 8
gcloud logging read \
  'resource.type="networkservices.googleapis.com/Gateway" AND httpRequest.requestUrl:*' \
  --project="${PROJECT_ID}" \
  --freshness=2h \
  --limit=100 \
  --format='value(httpRequest.status,jsonPayload.authzPolicyInfo.result,httpRequest.requestUrl)' \
  | sort -u | tee /tmp/cs-gateway-audit.txt

python3 - <<'PY'
from pathlib import Path
rows=[r for r in Path("/tmp/cs-gateway-audit.txt").read_text().splitlines() if r.strip()]
print(f"unique gateway rows: {len(rows)}")
for r in rows[:30]:
    print(r)
if not rows:
    raise SystemExit("FAIL: no Gateway audit rows yet")
print("PASS: Gateway audit evidence present")
PY
