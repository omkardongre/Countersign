# Test case: aurex / AX-3001

- Date run: 2026-08-17
- Mode: Run on the deployed Agent Runtime (checked)
- Engine: `projects/777448403573/locations/us-central1/reasoningEngines/6585223078622527488`

## Form input

| Field | Value |
|---|---|
| Tenant | `aurex` |
| Batch | `AX-3001` |
| Product | `AXR450 Sterile Injectable` |
| Document type | `Batch record` |
| Reported by | `Quality Control` |
| Description | `Synthetic demo: critical batch-record deviation detected during final review.` |
| Evidence refs | `BMR-AX-3001` |
| Patient safety impact | unchecked |
| Sterility assurance impact | checked |
| Affects release criteria | unchecked |
| Regulatory reportable | unchecked |
| Product quality impact | `confirmed` |
| Prior occurrences (90D) | `0` |

## Computed preview

- Severity: `CRITICAL`
- Trigger: `sterility_assurance_impact=true`

## Result

Blocked at `async_create_session` with
`403 Forbidden {'message': 'RBAC: access denied', 'status': 'Forbidden'}`.

### Root cause (proven, not assumed)

The denial is server-side, inside the engine — not the desk's credentials.

Engine stack trace (Cloud Logging):
`async_create_session` → `VertexAiSessionService.create_session` →
`vertexai._genai.sessions.create` → 403 RBAC.

Probes run with the desk's own ADC (`admin@countersign.site`):

| Probe | Result |
|---|---|
| `POST .../reasoningEngines/6585223078622527488/sessions` on `us-central1-aiplatform.googleapis.com` | **200** |
| same on global host `aiplatform.googleapis.com` | **200** |
| same with `locations/global` in the path | 404 `NOT_FOUND` (rules out the `GOOGLE_CLOUD_LOCATION=global` theory) |

So the sessions API, the resource, and project IAM are all fine. Confirmed the
engine's Agent Identity already holds `roles/aiplatform.user`,
`aiplatform.expressUser`, `aiplatform.agentDefaultAccess`, and `iap.egressor`
on both `ep-aiplatform-regional` and `ep-aiplatform-rep`.

The actual blocker is the authz policy `countersign-mcp-case-workflow-tools`
(`infra/geap/mcp-case-workflow-tools-allow.yaml`):

- `action: ALLOW`
- `target.resources` = the entire `countersign-egress` agent gateway
- `httpRules` match **only** MCP `tools/list` and a fixed set of `tools/call`

An ALLOW policy denies everything that does not match a rule, and emits exactly
`RBAC: access denied`. The engine's outbound call to the Vertex sessions API
matches no MCP rule, so all non-MCP egress from the engine is blocked —
including its own session storage.

This is why MCP department tools work but sessions do not.

### Fix required (infra, not app code)

Scope the MCP tool restriction to the MCP host only, and add an allow rule for
the aiplatform hosts, then re-apply the policy. No change needed in
`web/src/lib/live/`.

### Side finding

The live policy currently allows 6 tool names. The repo YAML also lists
`prior_art_query` and `prior_art_write`, so live is behind the repo — Case
Memory calls would be denied too, for the same reason.

### Fix applied live (2026-08-17)

- `mcp-case-workflow-tools-allow.yaml` and `mcp-qc-probe-lims-only.yaml`:
  added `hosts: [exact: countersign-department-mcp-777448403573.us-central1.run.app]`
  so the MCP tool-name ALLOW only governs MCP traffic, not everything on the
  gateway. Re-applied with `gcloud beta network-security authz-policies import`.
- New policy `countersign-aiplatform-sessions-allow.yaml`: `ALLOW` rule for
  hosts `aiplatform.googleapis.com` and `us-central1-aiplatform.googleapis.com`
  on the same `countersign-egress` gateway, so `async_create_session` /
  `async_get_session` / `:streamQuery` egress from the Runtime is no longer
  denied by default. Applied live with `gcloud beta network-security
  authz-policies import countersign-aiplatform-sessions-allow --source=...`.
- Confirmed live in Cloud console describe output — all three policies show
  `action: ALLOW` with the expected `httpRules` and `target.resources`
  pointing at `countersign-egress`.

#### Correction: the first attempt at this fix did not work

Listing only the plain hosts still denied every session call. The gateway log
for the denied request gave the actual hostname:

```
enforcedGatewaySecurityPolicy.hostname: us-central1-aiplatform.mtls.googleapis.com
requestWasTlsIntercepted: true
authzPolicyInfo.policies[].details: denied_as_no_allow_policies_matched_request
```

An Agent Identity presents an mTLS-bound client certificate, so its egress
lands on the `.mtls.` hostnames. Added
`us-central1-aiplatform.mtls.googleapis.com`, `aiplatform.mtls.googleapis.com`,
plus `telemetry.mtls.googleapis.com` / `telemetry.googleapis.com` — the
telemetry exports were being denied by the same rule, which silently disables
the Agent Observability traces `10` P4 / `11` Beat 5 depend on.

### Verified live after the fix

| Check | Result |
|---|---|
| `async_create_session` via `:query` | **200**, session id returned |
| `async_get_session` via `:query` | **200** |
| Full graph via `:streamQuery` | **200**, 35 events in 15.7s |
| Gateway decision for `...aiplatform.mtls...:appendEvent` | **ALLOWED / 200** |
| Gateway decision for MCP `initialize` | **ALLOWED** by `countersign-mcp-case-workflow-tools` (host scoping did not regress MCP) |

The graph now runs intake → `bmr_evidence_extractor` → departments and reaches
`await_sterility_incubation`.

### Two further bugs this unblocked and exposed

1. **`:query` envelope was not unwrapped** (`web/src/lib/live/engine-client.ts`).
   Every `:query` reply is wrapped in `{"output": {...}}`, so `session.id` was
   `undefined` and `session.events` was missing. The RBAC failure had been
   masking this — it would have broken the desk immediately after. Fixed by
   unwrapping `output` and normalising the session; also handles the live
   casing inconsistency (`async_create_session` returns snake_case
   `app_name`/`last_update_time`, `async_get_session` returns camelCase
   `appName`/`lastUpdateTime`).
2. **The MCP client sent no `Authorization` header** (`app/mcp/client.py`).
   `await_sterility_incubation` failed with an `ExceptionGroup` at
   `deviation_case@1/await_sterility_incubation@1`. Cloud Run log:
   `403 ... Empty Authorization header value`, while the gateway logged the
   same request `ALLOWED` — two layers, only one satisfied. Holding
   `roles/run.invoker` (which the identity does) is necessary but not
   sufficient; the caller must present an OIDC ID token whose audience is the
   service base URL. Fixed by minting one via
   `google.oauth2.id_token.fetch_id_token` and attaching it through
   `create_mcp_http_client(headers=...)` (this `mcp` version's
   `streamable_http_client` takes an `http_client`, not `headers`).

### Not yet verified

The MCP auth fix is code inside the deployed Runtime, so it does **not** take
effect until `deploy_case_runtime.py` is re-run. Until then the graph still
stops at `await_sterility_incubation`. Redeploy is delete + create of the
engine, so it needs an explicit go-ahead.
