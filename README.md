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
                     │  REQUEST_AUTHZ  → IAP / IAM   (identity)     │ proven
                     │  CONTENT_AUTHZ  → Model Armor (content)      │ proven
                     │  CONTENT_AUTHZ  → Countersign TQA extension  │ next
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
| Runtime | Agent Runtime, `gemini-3.6-flash` via Vertex AI |
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
| **Enforced deny proof, 9/9 checks** | **Done** — see below |
| **Model Armor as CONTENT_AUTHZ extension** | **Done** |
| Deterministic intake + severity classification | Done — `app/domain/`, see below |
| Multimodal evidence extraction, one BMR page | Done — `app/domain/`, see below |
| Deterministic compliance engine (completeness, OOS, OOT) | Done — `app/domain/`, see below |
| Parallel evidence branches (LIMS, EM, supplier CoA) | Not started |
| Deterministic GMP compliance engine | Not started |
| Investigation + Challenger agents (LLM) | Not started |
| MCP tool servers (MES, LIMS, historian, supplier, QMS) | Not started |
| TQA `ext_proc` extension | Not started — needs a VPC/DNS-peering spike |
| 14-day sterility wait, kill/resume | Not started |
| UI, demo | Not started |

### Domain workflow — deterministic intake and classification

The first node of the GMP deviation-investigation graph (`final_reseach/05_architecture.md`
§5/§11 step 1) is implemented in `app/domain/`:

- `models.py` — `CaseIntake` (structured detection payload) and `ClassifiedCase`
  (`tenant_id`, `lifecycle_state`, `decision_authority: "none"` on every record), following
  Veeva's Agent Card vocabulary (`final_reseach/02_veeva_vault_qms.md` §2)
- `classification.py` — pure-function minor/major/critical severity rules over structured
  signals (`patient_safety_impact`, `sterility_assurance_impact`, etc.), no model call, per the
  Annex 22 draft posture that the LLM is never in the critical decision path
- `intake.py` — wraps classification as an ADK `Workflow` `FunctionNode`, with input/output
  schemas inferred from type hints and enforced by the runtime

Verified against installed `google-adk` 2.6.3 source directly (`workflow/_function_node.py`),
not assumed from documentation. 14 unit tests in `tests/unit/test_classification.py` and
`tests/unit/test_intake.py`, run through a real `Workflow` + `InMemoryRunner`, not just the
bare function.

### Domain workflow — multimodal evidence extraction

The second stage (`final_reseach/05_architecture.md` §5, §11 step 4) is implemented as a static
ADK 2.x `Workflow` graph with exactly one model call inside it:

```
START → ingest_evidence_artifact   deterministic: MIME allow-list, SHA-256, tenant-scoped persist
      → extract_page_evidence      one narrow single-turn transcriber, provenance stamped in code
      → validate_page_evidence     deterministic: parse, spec checks, confidence routing, audit
```

- `master_data/bmr_page_spec_v1.json` + `master_data.py` — the approved field specification:
  what may be extracted, expected unit, acceptance range, and the confidence a transcription
  must reach. Versioned and change-controlled, with `approved_by` and `change_control_ref`, and
  every result records the spec version **and the SHA-256 of the exact spec bytes** so an
  auditor can prove which approved specification governed a given reading.
- `ingest.py` — narrow MIME allow-list checked before decode, size ceiling, content-addressed
  artifact filename (so an at-least-once resume cannot duplicate a source record), SHA-256 at
  the boundary, tenant written into artifact metadata for later re-verification. Integrity
  failures **raise**; they are not returned as values a downstream node could route around.
- `extraction.py` — one `Agent(mode="single_turn", include_contents="none")` with
  `output_schema=PageExtraction` and **no tools**. It transcribes characters and reports
  per-field legibility confidence; it does not classify, decide, or reach any system. Clean
  context per page is deliberate containment: externally-sourced documents such as supplier CoAs
  must not be able to steer the reading of the next page. Acceptance ranges are withheld from
  the model, because telling a transcriber that blender speed should be 8–14 rpm invites it to
  read 18 as 13 — and that out-of-range value is usually the deviation being investigated.
- `evidence_validation.py` — pure functions: syntactic parse, structural checks (pattern,
  allowed values, unit, usable source anchor), GMP semantic checks (acceptance range,
  chronology, recorded-duration agreement, independent second-person verification), then
  confidence routing with a higher bar for critical fields. Emits the review queue and an
  immutable audit event.

Design points that are load-bearing rather than stylistic:

- **The model transcribes; code interprets.** Readings arrive as verbatim strings. Every parse
  into a number, boolean or timestamp happens in deterministic code, so no rounding or unit
  conversion can happen inside a model call.
- **Provenance is stamped, never claimed.** The extractor cannot populate artifact identity.
  Code loads the artifact, re-verifies its SHA-256, MIME type and recorded tenant against the
  reference it was handed, and only then attaches identity to each reading. A hash or tenant
  mismatch terminates the node.
- **Units are never silently converted**, ambiguous numbers (`1,5`) and free-form dates are
  parse failures rather than guesses, and an out-of-range value is flagged **with its value
  preserved** — discarding it would delete the evidence.
- **Nothing here decides anything.** `EvidenceStatus` is `accepted` or
  `human_review_required` — whether a human must look, not whether the batch is acceptable.
  `decision_authority` stays `"none"` on every record.
- **Two disagreeing transcriptions of one field is a distinct finding from an unreadable one.**
  `conflicting_transcription` names both values a reviewer must resolve, rather than being
  folded into the same "extractor could not read this" diagnosis as a genuinely blank field.
- The loaded spec's version, hash and change-control reference are logged on load, so an
  operator can observe which approved specification a running instance is actually serving.

49 unit tests across `tests/unit/test_evidence_ingest.py`, `test_evidence_validation.py` and
`test_evidence_graph.py`. The graph tests run the full pipeline through a real `Workflow` +
`InMemoryRunner` with the transcriber replaced by a stubbed node returning known ground truth
(`tests/unit/evidence_fixtures.py`), so artifact persistence, hash and tenant re-verification,
provenance stamping, validation and routing are all verified without an API key or network.
Whether Gemini reads a particular smudged digit correctly is a separate model-level evaluation
question, not settled by these tests.

### Domain workflow — deterministic GMP compliance engine

The third stage (`final_reseach/05_architecture.md` §5, "Deterministic compliance engine")
runs after validation, still with zero model calls, and asks three separately regulated
questions rather than one blended verdict:

```
validate_page_evidence → evaluate_evidence_compliance
                              ├─ completeness   (ICH Q7 batch record review)
                              ├─ OOS            (FDA 1993/2022 out-of-specification guidance)
                              └─ OOT            (USP <1010> out-of-trend, separate from OOS)
```

- **Completeness** — every required field must be present *and settled*. A field still
  `review_required` (unverified low-confidence transcription) counts as incomplete: an
  unverified reading is not yet part of the record.
- **OOS (out-of-specification)** — every settled field validation already found `invalid`
  becomes a formal OOS finding with a **Phase I/Phase II gate**, per FDA's guidance and the
  Barr Laboratories precedent that retesting must not occur before a formal investigation is
  opened. Phase assignment is an allow-list: a field lands at Phase I ("no assignable cause
  yet") only when *every* finding on it is a pure range violation; any other finding
  (parse/pattern/unit/allowed-value/anchor problems) means an assignable cause is already on
  record and the field starts at Phase II. The observed out-of-range value is always preserved
  and cited — it is usually the deviation being investigated, never auto-resolved or hidden.
- **OOT (out-of-trend)** — a *separate* check against an approved historical baseline
  (`master_data/trend_baseline_axr450_v1.json`, its own change-control cadence, distinct from
  the field specification), evaluated only on fields validation settled as `accepted`. Per USP
  <1010>, a value can be simultaneously in-specification and out-of-trend — this is the whole
  point of OOT, and the engine evaluates it as a genuinely separate question, not a
  looser/tighter version of the OOS check. When no approved baseline exists for a product, OOT
  is explicitly **not evaluated** (`trend_baseline_id: null`) rather than silently reported as
  passing.
- **Root-cause tagging** — every finding carries a `root_cause_category` (material / machine /
  method / measurement / personnel / environment), set as master data on the field spec itself,
  matching Katalyze's published Deviation Investigator framing
  (`final_reseach/01_katalyze_ai.md` §3). This is a tag, not a determination: it gives the
  future Investigation agent a starting hypothesis dimension, never a root cause.

`ComplianceStatus` is `sufficient_for_investigation` or `insufficient_evidence` — still not a
disposition. `decision_authority` stays `"none"`. Every `ComplianceFinding` carries its
`SourceAnchor` where one exists, and the full `ValidatedEvidence` it was computed from travels
forward on the result, so a QP can audit a compliance finding without a separate lookup.

12 additional unit tests added since the initial review pass, specifically targeting the OOS
phase-assignment heuristic (every non-range finding shape — unit, blank transcription,
degenerate/unlocatable anchor, and a range finding mixed with a locatability finding — must land
at Phase II, not fall through to Phase I by default) and a guard against scoring a batch against
another product's trend baseline. Reviewed via `semantic_reviewer` sub-agent before commit; both
issues it found were fixed, not just noted.

Not yet wired: this graph is standalone. Joining it to `intake.py`, adding the parallel
LIMS/EM/supplier branches behind a `JoinNode` (the compliance engine already accepts one
`ValidatedEvidence` per artifact and would need a small reconciliation step, not a redesign, to
accept a joined dict of them), and replacing the scaffold agent in `app/agent.py` all come after
this slice is trusted.

---

## Governance proof

The control plane is verified by a gate that cannot pass falsely.
`infra/geap/verify_governance.sh` asserts nine conditions and exits non-zero on any of them:

1. `effectiveIdentity` is an org-scoped Agent Identity, **not** a service-account fallback
2. the Runtime instance is bound to the Agent Gateway
3. both authorization extensions are out of `DRY_RUN`, and IAP specifically has `failOpen: false`
4. a registered destination is reachable
5. an unregistered destination is blocked with 403 at the tool boundary
6. a benign prompt passes the Model Armor content extension
7. a prompt-injection style message is blocked by Model Armor with 403, before the model runs
8. Gateway request logs contain both an `ALLOWED` row and a `DENIED` row for the probe target
9. IAP recorded `granted=false` on `iap.webServiceVersions.egressViaIAP`

```bash
./infra/geap/verify_governance.sh
```

Current result, captured from the `hello_runtime` probe agent (its default model has since been
updated to `gemini-3.6-flash` to match the rest of the fleet; this transcript is left verbatim as
the actual run record rather than edited to match):

```
effectiveIdentity: agents.global.org-334195573957.system.id.goog/resources/aiplatform/
                   projects/777448403573/locations/us-central1/reasoningEngines/9119834875157807104
countersign-iap-request-authz: iamEnforcementMode=ENFORCED failOpen=False
countersign-model-armor:       iamEnforcementMode=ENFORCED failOpen=True
200 ALLOWED  https://aiplatform.mtls.googleapis.com/.../gemini-3.5-flash:generateContent
403 DENIED   https://httpbin.org/get
tool result: http_error status=403 reason=Forbidden
benign prompt: completes normally, finish_reason=STOP
injection prompt: 403 Security Policy Violation — blocked before the model was called
IAP: granted=false on iap.webServiceVersions.egressViaIAP
=== ALL 9 CHECKS PASSED ===
```

Note the deliberate asymmetry: IAP is `failOpen: false` because an outage on the identity/egress
border must not silently admit traffic. Model Armor is `failOpen: true`, matching Google's own
reference configuration — an availability incident in content screening degrades to unscreened
traffic rather than blocking the whole fleet. That is a content-safety tradeoff, not an
identity/egress hole, and the gate encodes the distinction rather than treating both the same.

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

### 3b. Model Armor content extension

Create a template, grant IAM to both the gateway and Reasoning Engine service agents (required
even when gateway and template share a project), then attach the extension:

```bash
# Template: see infra/geap/model-armor-authz-extension.yaml for the template ID referenced.
# Create via REST — the `gcloud model-armor templates create` subcommand has a permission
# check bug independent of actual IAM; the REST API with the same credentials works.

for SA in "service-${PROJECT_NUMBER}@gcp-sa-dep.iam.gserviceaccount.com" \
          "service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"; do
  for ROLE in roles/modelarmor.calloutUser roles/serviceusage.serviceUsageConsumer roles/modelarmor.user; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${SA}" --role="$ROLE" --condition=None --quiet
  done
done

gcloud service-extensions authz-extensions import countersign-model-armor \
  --source=infra/geap/model-armor-authz-extension.yaml --location=us-central1

gcloud beta network-security authz-policies import countersign-model-armor \
  --source=infra/geap/model-armor-authz-policy.yaml --location=us-central1
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

Note `gemini-3.6-flash` resolves via the **global** Vertex hostnames. `GOOGLE_CLOUD_LOCATION`
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

./infra/geap/verify_governance.sh   # expect 9/9
```

---

## Repository layout

```
app/                              agents-cli scaffold (FastAPI, A2A, Runtime adapter)
  domain/                             GMP deviation-investigation workflow
    models.py                           case intake + classified case
    classification.py                   deterministic severity rules
    intake.py                           intake FunctionNode
    evidence_models.py                  provenance, extraction and validation schemas
    master_data.py                      approved spec + trend baseline loaders (versioned + hashed)
    master_data/bmr_page_spec_v1.json   the approved BMR page field specification
    master_data/trend_baseline_axr450_v1.json  approved historical-trend baseline
    ingest.py                           deterministic artifact ingest
    extraction.py                       the one narrow multimodal transcriber
    evidence_validation.py              deterministic validation + confidence routing
    compliance_models.py                completeness / OOS / OOT finding schemas
    compliance.py                       the deterministic compliance engine
    evidence_graph.py                   the evidence-extraction + compliance Workflow graph
infra/geap/
  constants.env                       shared resource names
  agent-gateway-egress.yaml           Agent-to-Anywhere gateway spec
  iap-request-authz-extension*.yaml   dry-run and enforced variants
  iap-request-authz-policy.yaml       binds the IAP extension to the gateway
  model-armor-authz-extension.yaml    Model Armor CONTENT_AUTHZ extension
  model-armor-authz-policy.yaml       binds the Model Armor extension to the gateway
  register_platform_endpoints.sh      registers endpoints + grants roles/iap.egressor
  verify_governance.sh                9-check governance gate
  evidence/                           captured proof from the last verification run
  hello_runtime/                      minimal agent used for the governance proof
tests/                                unit, integration, eval
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
- Gateway custom authorization policy budget is 4 per Agent-to-Anywhere gateway. IAP and Model
  Armor use 2; the TQA extension will use a 3rd, leaving 1 in reserve.
- IAP and Model Armor are configured with different `failOpen` values on purpose (`false` vs
  `true`) — see [Governance proof](#governance-proof). This is stated explicitly rather than
  left as an unexplained inconsistency.
- Scaffolded with [`google/agents-cli`](https://github.com/google/agents-cli); reference
  samples under `research/reference-repos` informed the gateway and workflow patterns.
- The ADK `high-volume-document-analyzer` sample informed the ingest allow-list and bounded
  concurrency approach, but its fetch path disables TLS certificate verification. That is
  **rejected**, not adapted: a GxP system that trusts unverified transport has no provenance
  claim to make. The `invoice-processing` sample's versioned master-data loader is adopted; its
  autonomous decide-and-rewrite flow is not.
- `app/agent.py` still holds the `agents-cli` scaffold agent (weather/time). The domain graph is
  not yet mounted on the Runtime entrypoint.

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
