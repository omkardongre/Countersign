# Countersign

An enterprise agent control plane for contract pharmaceutical manufacturing, built on the
Gemini Enterprise Agent Platform. Countersign makes a CDMO's **Technical Quality Agreement**
machine-executable, and demonstrates it through GMP deviation investigation and batch
disposition.

Google's Agent Gateway answers *who* and *what*. Countersign answers *whether the Quality
Agreement permits this party to assess this thing, at this lifecycle stage, for this sponsor*.

> **Status: control plane proven, domain fleet in progress.** See
> [Build status](#build-status). Nothing in this README describes behaviour that is not
> currently running — where something is unbuilt it is marked as such.

---

## Why this exists

A deviation is flagged on a 2,000-litre batch. The batch goes on hold; it is worth millions.
Three engineers spend two weeks on it. Almost none of that time is spent reasoning about the
science — chromatography data is in one system, environmental monitoring in another, the batch
record is part electronic and part paper, and prior similar deviations sit in a quality system
that talks to neither. The reasoning takes an afternoon. Finding the evidence takes the
fortnight.

Two regulators have converged on the same constraint for automating this work:

- **Draft EU GMP Annex 22** excludes generative AI and LLMs from critical GMP applications, and
  requires a qualified human in the loop for non-critical use.
- **FDA Warning Letter 320-26-58** (April 2026) cited a firm for releasing AI-generated
  specifications and master production records into manufacturing without Quality Unit review.

So Countersign is built on one non-negotiable rule:

> **The LLM is never in the critical decision path.** Agents assemble, extract, cite and
> propose. Every determination touching patient safety, product quality or data integrity is
> made by a qualified human. The system's job is to make that review fast and completely
> traceable.

---

## Architecture

```
                     ┌─────────────────────────────────────────────┐
 Qualified Person ──▶ │  UI  (review, citations, personal sign-off) │
                     └─────────────────────────────────────────────┘
                                        │
                     ┌──────────────────▼──────────────────────────┐
                     │  Agent Runtime — ADK Workflow graph          │
                     │  intake → evidence extraction → compliance   │
                     │  engine → investigation → challenger →       │
                     │  release gate → RequestInput (QP)            │
                     └──────────────────┬──────────────────────────┘
                                        │  every egress, Agent Identity (SPIFFE)
                     ┌──────────────────▼──────────────────────────┐
                     │            Agent Gateway                     │
                     │  REQUEST_AUTHZ  → IAP / IAM   (identity)     │
                     │  CONTENT_AUTHZ  → Model Armor (content)      │
                     │  CONTENT_AUTHZ  → Countersign TQA extension  │
                     │                   (tenant, lifecycle state)  │
                     └──────────────────┬──────────────────────────┘
                                        │  deny by default
                MES · LIMS · historian · QMS · supplier portal · Drive
```

| Layer | Implementation |
|---|---|
| Registry | Agent Registry — agents, MCP servers, endpoints |
| Identity | Agent Identity — SPIFFE ID per agent, mTLS + DPoP |
| Gateway | Agent Gateway, Agent-to-Anywhere (egress) mode |
| Content guardrail | Model Armor as a `CONTENT_AUTHZ` authorization extension |
| **Domain policy** | **Countersign TQA `ext_proc` extension — ours** |
| Runtime | Agent Runtime, `gemini-3.5-flash` via Vertex AI |
| Durable state | Cloud SQL Postgres via ADK `DatabaseSessionService` |
| Case memory | Firestore + embeddings (provenance and tenant filtering that Memory Bank cannot round-trip) |
| Observability | Agent Observability, Cloud Trace, Cloud Logging |

---

## Build status

| Component | State |
|---|---|
| Cloud Organization, project, APIs | Done |
| Agent Registry + platform endpoints | Done |
| Agent Gateway (Agent-to-Anywhere) | Done |
| Agent Identity on Runtime + gateway binding | Done |
| **Enforced deny proof, 7/7 checks** | **Done** — see below |
| Model Armor as CONTENT_AUTHZ extension | Next |
| Domain Workflow graph and MCP tool servers | Not started |
| TQA `ext_proc` extension | Not started — needs a VPC/DNS-peering spike |
| 14-day sterility wait, kill/resume | Not started |
| UI, demo | Not started |

---

## Governance proof

The control plane is verified by a gate that cannot pass falsely.
`infra/geap/verify_governance.sh` asserts seven conditions and exits non-zero on any of them:

1. `effectiveIdentity` is an org-scoped Agent Identity, **not** a service-account fallback
2. the Runtime instance is bound to the Agent Gateway
3. the authorization extension is enforcing — fails on `iamEnforcementMode: DRY_RUN` or `failOpen: true`
4. a registered destination is reachable
5. an unregistered destination is blocked with 403 at the tool boundary
6. Gateway request logs contain both an `ALLOWED` row and a `DENIED` row for the probe target
7. IAP recorded `granted=false` on `iap.webServiceVersions.egressViaIAP`

```bash
./infra/geap/verify_governance.sh
```

Current result:

```
effectiveIdentity: agents.global.org-334195573957.system.id.goog/resources/aiplatform/
                   projects/777448403573/locations/us-central1/reasoningEngines/9119834875157807104
countersign-iap-request-authz: iamEnforcementMode=ENFORCED failOpen=False
200 ALLOWED  https://aiplatform.mtls.googleapis.com/.../gemini-3.5-flash:generateContent
403 DENIED   https://httpbin.org/get
tool result: http_error status=403 reason=Forbidden
IAP: granted=false on iap.webServiceVersions.egressViaIAP
=== ALL 7 CHECKS PASSED ===
```

Machine-readable evidence is written to `infra/geap/evidence/`.

---

## Spin-up

### Prerequisites

- A **Google Cloud Organization**. Agent Identity principals are org-scoped
  (`principal://agents.global.org-ORG_ID...`) and there is no org-less form, so a project
  attached only to a personal account cannot complete this setup. Cloud Identity Free plus a
  domain you own is sufficient.
- `gcloud` authenticated as a user with org-level IAM, and Python 3.12+ with `uv`.

### 1. Project and configuration

Create a project **inside the organization** (pre-existing projects do not migrate), link
billing, then set your values in `infra/geap/constants.env`:

```bash
PROJECT_ID=...
PROJECT_NUMBER=...
ORG_ID=...
LOCATION=us-central1
AGENT_GATEWAY=countersign-egress
STAGING_BUCKET=gs://.../agent-staging
```

### 2. Enable APIs

```bash
gcloud services enable \
  aiplatform.googleapis.com agentregistry.googleapis.com \
  networkservices.googleapis.com networksecurity.googleapis.com \
  iap.googleapis.com modelarmor.googleapis.com \
  compute.googleapis.com dns.googleapis.com \
  logging.googleapis.com cloudtrace.googleapis.com telemetry.googleapis.com \
  --project="$PROJECT_ID"
```

### 3. Agent Gateway and authorization policy

```bash
gcloud network-services agent-gateways import countersign-egress \
  --source=infra/geap/agent-gateway-egress.yaml --location=us-central1

# Deploy the IAP authorization extension in dry run first.
gcloud service-extensions authz-extensions import countersign-iap-request-authz \
  --source=infra/geap/iap-request-authz-extension.yaml --location=us-central1

gcloud beta network-security authz-policies import countersign-iap-request-authz \
  --source=infra/geap/iap-request-authz-policy.yaml --location=us-central1
```

### 4. Deploy the Runtime agent

Both `identity_type=AGENT_IDENTITY` and `agent_gateway_config` are required. Omitting the
former fails **silently** — `effectiveIdentity` falls back to the default Vertex service
account and the agent is quietly ungoverned.

```bash
uv sync
uv run python infra/geap/hello_runtime/deploy_agent.py
```

Deploy from **source** rather than a custom container: the gateway's TLS CA is injected
automatically on the source path, and a BYOC image fails with `CERTIFICATE_VERIFY_FAILED`.

### 5. Register platform endpoints

Under enforcement the gateway denies internal Google APIs too, so they must be registered and
granted `roles/iap.egressor`.

```bash
./infra/geap/register_platform_endpoints.sh
```

Note `gemini-3.5-flash` resolves via the **global** Vertex hostnames. `GOOGLE_CLOUD_LOCATION`
is `global` on the Runtime instance while the Runtime region stays `us-central1`.

### 6. Verify, then enforce

```bash
./infra/geap/verify_governance.sh   # will fail while still in DRY_RUN — that is intended
```

Once ALLOWED rows appear, enforce by **removing** `iamEnforcementMode: "DRY_RUN"` from the
extension metadata (there is no `ENFORCE` value to set) and re-importing:

```bash
gcloud service-extensions authz-extensions import countersign-iap-request-authz \
  --source=infra/geap/iap-request-authz-extension-enforced.yaml --location=us-central1

./infra/geap/verify_governance.sh   # expect 7/7
```

---

## Repository layout

```
app/                              agents-cli scaffold (FastAPI, A2A, Runtime adapter)
infra/geap/
  constants.env                   shared resource names
  agent-gateway-egress.yaml       Agent-to-Anywhere gateway spec
  iap-request-authz-extension*.yaml   dry-run and enforced variants
  iap-request-authz-policy.yaml   binds the extension to the gateway
  register_platform_endpoints.sh  registers endpoints + grants roles/iap.egressor
  verify_governance.sh            7-check governance gate
  evidence/                       captured proof from the last verification run
  hello_runtime/                  minimal agent used for the governance proof
tests/                            unit, integration, eval
```

---

## Honesty notes

- **System integrations are real. Pharmaceutical content is synthetic.** The CDMO, its
  sponsors, the batches and the Qualified Person are fictional and labelled as such. Putting
  real GMP batch data in a demo would itself be the compliance violation.
- **Annex 22 is a draft.** Consultation closed October 2025; final text is targeted for Q4
  2026. Its exclusion of LLMs from critical applications is why the LLM here is not in the
  decision path.
- ADK is pinned at **2.6.3**. The workflow replay and HITL modules it depends on are
  underscore-prefixed internals; a minor version bump could break resume.
- `integrations/agent_registry` is **read-only** — agents are registered there and read back,
  with a separate write path for status changes.
- Custom authorization extensions do **not** validate the callout server's TLS certificate;
  reachability is constrained to a VPC-internal FQDN instead.
- Scaffolded with [`google/agents-cli`](https://github.com/google/agents-cli); reference
  samples under `research/reference-repos` informed the gateway and workflow patterns.

---

## Sources

- [Agent Gateway overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/agent-gateway-overview)
- [Set up Agent Gateway](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/set-up-agent-gateway)
- [Delegate authorization with Service Extensions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/delegate-authorization)
- [Route Agent Runtime traffic through Agent Gateway](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-gateway-runtime-deploy)
- [Troubleshoot Agent Gateway connectivity](https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/troubleshoot-agent-gateway)
- [Authenticate using an agent's own authority](https://docs.cloud.google.com/iam/docs/auth-agent-own-identity)
- [EU GMP Annex 22 draft (EC consultation)](https://health.ec.europa.eu/document/download/5f38a92d-bb8e-4264-8898-ea076e926db6_en)
- [FDA Warning Letter 320-26-58](https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/warning-letters/purolea-cosmetics-lab-722591-04022026)
