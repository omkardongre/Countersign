# Countersign

An enterprise agent control plane for contract pharmaceutical manufacturing, built on the
Gemini Enterprise Agent Platform. Countersign makes a CDMO's **Technical Quality Agreement**
machine-executable, and demonstrates it through GMP deviation investigation and batch
disposition.

Google's Agent Gateway answers *who* and *what*. Countersign answers *whether the Quality
Agreement permits this party to assess this thing, at this lifecycle stage, for this sponsor*.

> **Status: control plane proven, eight core case/sponsor agents live, real department systems and sponsor
> liaison deployed.** See [Build status](#build-status). **CFI Phases 1–3 are done and proven live**,
> including real Calendar and Drive/Docs artifacts filed via Workspace domain-wide delegation.
> Implementation is now paused at this point by decision — remaining CFI capabilities (Gmail vendor
> interviews, and everything cut in Phase 4) are optional future work, not committed scope. Current
> focus is end-to-end testing, documentation, and demo preparation. Nothing in this README describes
> behaviour that is not currently running — where something is unbuilt or not live it is marked as such.
>
> **2026-08-20 — fleet conversion (P-A).** The five department/reasoning nodes that used to be
> plain Python functions and no-tool LLM calls are now real `google.adk.agents.Agent(mode=
> "single_turn")` nodes, each bound to its own MCP-backed tool(s), placed directly in the
> `Workflow` graph. Deterministic checks still run only on each tool's captured typed
> snapshot, never on an agent's own text. See [Real agent fleet](#real-agent-fleet).
>
> **2026-08-26 — model repin (temporary) and live fleet status.** Every agent is currently
> pinned to `gemini-3.5-flash-lite` for fast/cheap iteration during testing — a deliberate,
> temporary choice, not a final model decision; the plan is to repin to the strongest model
> priced at or below `gemini-3.6-flash` (check current names/pricing at
> [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models)) before
> the final proof run and demo. The same pass fixed a real bug in the live desk (the projector
> was missing the `collect_environmental_monitoring` branch) and added `GET /fleet/status`, a
> live Agent Registry read-back layered on the static fleet manifest — see
> [Real agent fleet](#real-agent-fleet).
>
> **2026-08-27 — case-desk prod-level cleanup.** Three GUI-testing findings closed (see
> `final_reseach/12_bug_fixes.md`, `checklist.md`): demo fixtures are now opt-in only
> (`COUNTERSIGN_ENABLE_FIXTURES`, default off) instead of always seeded; the sterility
> incubation hold is a required, QA-set per-case value with no platform default (removed the
> prior 14-day fallback entirely); and every raw internal source URI shown in the case/CFI
> panels now renders a human-readable label by default, with the exact URI still available on
> demand for audit.
>
> **2026-08-28 — item 5, SENAITE LIMS backend for QC.** `SenaiteLimsSource` reads real QC
> analysis results from a self-hosted SENAITE instance over `senaite.jsonapi`, selected via
> `COUNTERSIGN_LIMS_BACKEND=senaite` alongside the existing Cloud SQL backend. Deployed live on
> the department MCP with credentials from Secret Manager. Real, manually-entered SENAITE data
> (no seed scripts) confirmed for two clients and four batches, including one deliberately
> out-of-spec result. A live end-to-end GUI run for `ACME01`/`BATCH-2026-001` now passes all
> four department branches, completes the sterility hold, and reaches the QP panel; the Annex 16
> gate correctly returns `not_ready` because the investigation is still open. Also in
> this pass: removed the unused `evidence_refs` intake field end to end (backend, tests,
> frontend) rather than leave it half-implemented; fixed a Drive Picker bug where the "choose
> file" control could get stuck if the picker was dismissed without using its own Cancel
> control; fixed a case-desk pipeline step that always displayed "14-day hold" regardless of
> the case's actual configured duration.

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
                     │  intake → 4 parallel dept. agents (real,     │
                     │  tool-bound) → compliance engine →           │
                     │  investigation agent → challenger agent →    │
                     │  release gate → RequestInput (QP)             │
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
| Runtime | Agent Runtime, `gemini-3.5-flash-lite` via Vertex AI |
| Durable state | Cloud SQL Postgres via ADK `DatabaseSessionService` |
| Case memory | Firestore + embeddings (provenance and tenant filtering that Memory Bank cannot round-trip), or BigQuery `VECTOR_SEARCH` (doc `12` T1-4) behind the same interface — explicit opt-in per deployment, unset keeps Firestore |
| Controlled guidance | Vertex AI RAG Engine via department MCP: separate tenant-scoped SOP and public-guidance corpora, manifest-verified citations, deterministic SOP gate |
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
| Deterministic intake + severity classification | Done — `app/domain/` |
| Multimodal evidence extraction, one BMR page | Done — `app/domain/` |
| Deterministic compliance engine (completeness, OOS, OOT) | Done — `app/domain/` |
| Joined case `Workflow` on `app.agent` | Done — `deviation_case` |
| Parallel department collectors (BMR, LIMS, EM, CoA) | Done — synthetic catalog, `JoinNode` |
| Investigation + Challenger + citation gate | Done — LLM drafts; code gates citations |
| Deterministic Annex 16 §1.7 confirmation checklist | Done — code; never certifies |
| QP `RequestInput` + case `ResumabilityConfig` | Done — confirm refused while `not_ready` |
| MCP department tools (`lims_query`, `em_query`, `coa_fetch`) | Done — FastMCP; Runtime requires `COUNTERSIGN_MCP_URL`; live Gateway 403 not attached |
| TQA tenant + lifecycle `ext_proc` | Done live — callout denies cross-tenant and `dispositioned`; `countersign-egress` has VPC + DNS peering; Runtime MCP 403 not claimed |
| 14-day sterility wait, kill/resume | Cloud SQL live — `countersign-sessions` Postgres 16, IAM auth, ADK `sessions`/`events` + `sterility_incubation` proven. Clock reached via MCP (`sterility_start`/`sterility_get`/`sterility_bind_resume_handle`), tenant-checked, atomic tick claim |
| Scheduler wake-up (`POST /scheduler/sterility-tick`) | Done live — Cloud Scheduler job `countersign-sterility-tick` (OIDC, `*/15 * * * *`) hits the deployed tick Cloud Run service; manual trigger returns `200` |
| Case-workflow Agent Runtime deploy | Done live — `infra/geap/deploy_case_runtime.py` run against `countersign-505107`; reasoningEngine `4625084117367652352`. Uses Vertex-managed sessions (Agent Identity cannot hold Cloud SQL IAM credentials) |
| Case Memory (Firestore + embeddings) | Done live — Native `(default)` in `us-central1`; indexes `READY`; MCP Cloud Run `countersign-department-mcp` serves `prior_art_*` (`verify_mcp_case_memory.py`, embedded KNN). Runtime prior-art on a live case and Gateway 403 are not claimed |
| Controlled SOP/public guidance RAG | Implemented, not live — Vertex RAG Engine retrieval, separate corpora, manifest verification, tenant CEL filtering, MCP isolation, and deterministic controlled-SOP citation gate. Manual corpus creation/import, Drive IAM, manifest population, MCP env configuration, redeploy, and live verification remain required |
| **T1-7 public EudraGMDP/openFDA screening** | **Implemented, not live** — official EudraGMDP non-compliance HTML and openFDA drug-enforcement clients, strict parsing/provenance/bounded cache, dedicated read-only MCP boundary, typed `screening_unavailable`, advisory-only case/report fields, and private Cloud Run/Gateway artifacts. No deployment or Gateway application was performed; public CoA identity population and runtime IAM/URL configuration remain manual |
| UI, demo | Case desk in `web/` — queue, case, open intake, Annex 16 + QP panel, and the CFI desk panel for labelled fixture/live event state. Live Agent Runtime path wired and run live: `streamQuery` start, poll-based progress (no held SSE), `RequestInput` resume for QP confirm. Set `COUNTERSIGN_CASE_ENGINE_URL` + ADC. The CFI panel consumes separate CFI Runtime events when supplied; it does not start or resume the CFI Runtime from the browser. A projector bug that misread which node paused (fixed 2026-08-18) is why an earlier live run appeared stuck on `Classify`; a second projector gap (missing `collect_environmental_monitoring` branch) was fixed 2026-08-26 — see [Real agent fleet](#real-agent-fleet). **2026-08-27:** demo fixtures are opt-in only (`COUNTERSIGN_ENABLE_FIXTURES`, default off — production queues show only real cases); the sterility hold is a required per-case QA value with no platform default; raw internal source URIs render human-readable labels by default with the exact URI available on demand. Architecture diagram and demo film not started |
| **Fleet live status (`GET /fleet/status`)** | **Done (2026-08-26)** — layers a live Agent Registry read-back onto the static fleet manifest via the real `google.adk.integrations.agent_registry.AgentRegistry` client. See [Real agent fleet](#real-agent-fleet). |
| Manufacturing evidence over Drive | Done live (2026-08-19) — `drive_fetch_evidence` MCP tool reads a real scanned BMR page from Google Drive; `collect_manufacturing` pulls it by file id instead of trusting pushed bytes. No placeholder evidence remains on the live path |
| **P-L: real multi-day sterility hold (uncompressed)** | **Done live (2026-08-20)** — a disposable case (`aurex`/`LONGHOLD-PL-01`) opened on the deployed Runtime with no `COUNTERSIGN_STERILITY_HOLD_SECONDS` set. Confirmed from the live session event: `compressed_for_demo: false`, `hold_seconds: 1209600` (14 days exactly), `started_at: 2026-08-20T17:58:11Z`, `due_at: 2026-09-03T17:58:11Z`. Not to be ticked/resumed before `due_at` — it is the real wall-clock resume proof for the demo film. Details in `checklist.md` |
| **P-A: real agent fleet conversion** | **Done live (8 of 8, 2026-08-23)** — all target agents are real: six tool-bound/structured ADK graph agents plus the separate sponsor A2A peer. Fleet manifest available at `GET /fleet/manifest` |
| **P1: real Cloud SQL LIMS / BigQuery EM** | **Done live (2026-08-23)** — provisioned `countersign_lims.lims_results` and `countersign_em.em_readings`; deployed department MCP with `COUNTERSIGN_LIMS_BACKEND=cloud_sql` and `COUNTERSIGN_EM_BACKEND=bigquery`. Authenticated live MCP calls for `aurex`/`AX-2291` returned `system=cloud_sql` and `system=bigquery`. LIMS remains SELECT-only and EM has Data Viewer/Job User only. Supplier CoA (untrusted channel) and QMS write-back remain separate work |
| **Item 5: SENAITE LIMS backend for QC** | **Done live (2026-08-29)** — `SenaiteLimsSource` over `senaite.jsonapi` read the real SENAITE `AnalysisRequest` for `ACME01`/`BATCH-2026-001` through the deployed case Runtime; QC returned `OK` (98.4 %LC, spec 95–105). All four department branches passed, the sterility hold completed, the Annex 16 gate correctly returned `not_ready` while the investigation is open, and the QP panel recorded `cannot_confirm`. The same backend setup, credential discipline, and manually-entered data from the 2026-08-28 entry remain unchanged. |
| **P3: sponsor liaison A2A peer (separate service)** | **Done live (2026-08-23)** — separate Cloud Run A2A service with per-sponsor API-key auth, a real tool-bound agent, `case_status` Cloud SQL store, department MCP scope enforcement, and a dedicated Gateway tool policy. Live proof: authorized sponsor returns the real hold status; cross-tenant query is denied; unknown batch returns `not_found`; missing/wrong key returns 401 |
| **CFI Phase 1: Cross-Functional Investigation core** | **Done live (2026-08-24)** — deterministic trigger/ledger/gates/budget, a separate CFI Agent Identity Runtime (`reasoningEngines/7328770015929303040`, from `app.cfi_app`), `cfi_lead_agent` + `scope_expansion_agent`, tenant-locked `sibling_batch_query`, and a live Cloud SQL/MCP-backed durable CFI store (`adk_sessions.cfi_investigation`, MCP identity SELECT/INSERT/UPDATE only, no DELETE/DDL) with atomic idempotent open and optimistic-concurrency save. `escalate_to_cfi` remains a parallel terminal branch off `gate_investigation` — the proven release/QP path is unchanged. `sibling_batch_query` is live on Cloud SQL (`COUNTERSIGN_SIBLING_BATCH_BACKEND=cloud_sql`, `batch_material_lots`, SELECT-only) on the redeployed department MCP, alongside unchanged real LIMS/EM. A dedicated Gateway policy (`countersign-mcp-cfi-workflow-tools`) exposes exactly `sibling_batch_query`/`cfi_open`/`cfi_get`/`cfi_save`; the Runtime has its own Agent Registry entry and its own `countersign-cfi-invoker` credential boundary, separate from the case Runtime's. **Live bounded probe passed:** a real CFI (tenant `aurex`, batch `AX-2291`) was opened, invoked on the deployed CFI Runtime (29 streamed events, no errors), returned real same-tenant Cloud SQL siblings (`AX-2292`/`AX-2293`), rejected a cross-tenant sibling query, and closed durably at `revision=2` with `outcome=root_cause_identified`, one hypothesis, and evidence recorded from `sibling_batch_query` — independently re-read through MCP, not just observed mid-run. The probe row is retained by design (no delete operation on this MCP surface). See `checklist.md` |
| **CFI Phase 2: Chat interview loop** | **Done live (2026-08-25)** — real Google Chat adapter (`chat_send_question`/`chat_read_replies`, idempotent send, deterministic untrusted-reply classification with prompt-injection markers forced to `out_of_scope`), a `RequestInput` interview node on the CFI graph, a scheduler control-plane tool `cfi_interview_tick` (claims bounded leases, sends bounded nudges, never exposes raw reply text), `POST /scheduler/cfi-tick`, a Cloud-Run caller-identity gate scoping the tick tool (it never crosses Agent Gateway), and Cloud SQL/Gateway/deploy-script wiring. 15 focused tests pass; `uv run pytest tests/unit -q` is 245 passed / 3 pre-existing unrelated failures. **Live proof:** `countersign.site` upgraded to Google Workspace, a real Chat app installed with admin-approved data access, `cfi_interview` provisioned on Cloud SQL, department MCP and tick service redeployed with real Chat/tick-invoker config, the Gateway policy updated, and a second Cloud Scheduler job created — then a real question was sent to a live Chat space and a real human reply was captured, correctly sender/thread-verified and classified, with `decision_authority` staying `"none"`. See `checklist.md`. |
| **CFI Phase 3: trend correlation, Calendar review, filed report** | **Done live (2026-08-25)** — three real tenant-locked trend sources (EM BigQuery, cross-batch LIMS Cloud SQL, and prior-CFI recurrence), deterministic three-outcome closure, server-owned Calendar site-review scheduling, retry-safe typed Calendar/Docs references and errors, a write-only `cfi_reporter_agent`, fleet manifest at exactly 14 entries, and a CFI desk panel with hypothesis ledger, captured evidence, interview status, budget counters, activity, and artifact references. **Live proof:** a real CFI (`aurex`/`AX-2291`) was opened and invoked on the redeployed CFI Runtime (`reasoningEngines/3969506906375979008`, 39 streamed events, no errors), returned real same-tenant Cloud SQL siblings, rejected a cross-tenant sibling query, and closed durably (`outcome=root_cause_identified`, revision 4). Both post-closure artifacts filed for real: a Calendar site-review event with a real invited attendee via keyless Workspace Domain-Wide Delegation on `c_9cf7bc86c8c01aaae8cd9e657ebe842e13799a49489c7e05ccbc31012d2373c0@group.calendar.google.com`, and a filed CFI report Google Doc (`1g68iGKDIZZ5f2aWwt3EVT30dYrTt4-8aR8QnpXqhFqE`) in the tenant's Drive folder, also via DWD — service accounts have no Drive storage quota of their own, so both artifacts share one keyless-signed-JWT delegated identity (`COUNTERSIGN_DWD_SUBJECT`/`COUNTERSIGN_DWD_SIGNER` in `app/app_utils/services.py`, `calendar_tools.py`, `cfi_report_writer.py`). The Calendar retry path compares whole-second instants (`calendar_tools.py::site_review_window`), matching Calendar's own persisted precision, so an idempotent resume never gets rejected as a moved event. `evaluate_cfi_closure` is idempotent (`cfi_gate.py`) so a rerun after closure cannot re-stamp `closed_at` under a stable Calendar event id. See `checklist.md`. **CFI implementation is paused here by decision (2026-08-25) — Phases 1–3 are the complete, shipped scope.** Gmail vendor interviews and everything else previously scoped as Phase 4 are optional future work, not committed. |

### Real agent fleet

Per `final_reseach/ARCHITECTURE.md` §5 ("the fleet must be real agents"),
verified against ADK 2.6.3 and the official
[graph-based workflow docs](https://google.github.io/adk-docs/graphs/): an ADK `Agent` is a
`BaseNode`, so a real, tool-bound `Agent(mode="single_turn")` can sit directly inside the
`Workflow` graph alongside deterministic function nodes — no rewrite, no lost durability.

`GET /fleet/manifest` (`app/domain/fleet_manifest.py`) reports every real agent node live:

| Agent | Tools | What decides the outcome |
|---|---|---|
| `manufacturing_evidence_agent` | `drive_fetch_evidence` | Agent decides to fetch; fetched bytes are captured from the real tool call and handed to the deterministic ingest → extract → validate → compliance chain unchanged |
| `qc_lab_agent` | `lims_query` | Agent decides to call it; `check_lims` runs on the tool's captured `LimsSnapshot`, never on the agent's text |
| `environmental_monitoring_agent` | `em_query` | Agent decides to call it; `check_em` runs on the tool's captured `EmSnapshot`. Split from `qc_lab_agent` into its own agent/department — separate data system (BigQuery vs LIMS Cloud SQL) |
| `supply_assurance_agent` | `coa_fetch`, `eudragmdp_screen`, `openfda_enforcement_screen` (public read-only screening) | Agent decides to call the tenant-locked CoA tool first and the two dedicated public screens once each; only safe screening status metadata reaches the model; `check_coa` runs on the captured `CoaSnapshot`, while regulator results remain typed advisory context outside compliance findings |
| `deviation_investigator` | `prior_art_query` | May re-query prior deviations at most once; any fact it surfaces becomes citeable only because it was captured from the real tool call, never by the agent's assertion. Citations are checked afterwards by the deterministic `investigation_gate` |
| `investigation_challenger` | none, by design | Runs with a clean context (no shared history with the investigator) so it cannot rubber-stamp its own prior wording. Attacks are recorded for the QP; they do not themselves fail the gate |

Every agent's tool calls run against tenant/batch-locked closures — the model supplies no
scope arguments, so it cannot query another tenant's or batch's data. A branch fails closed if
its required CoA/evidence tool is not captured; an unavailable public screening result remains
explicit advisory context and does not by itself gate the case.

The manifest now also includes the CFI Runtime fleet and the existing workflow/control-plane
entries, for exactly 14 entries: `deviation_case` (workflow), the four department collectors,
`deviation_investigator`, `investigation_challenger`, `qa_qms_write_agent`, the separate
`sponsor_liaison_agent`, and the five CFI agents `cfi_lead_agent`, `scope_expansion_agent`,
`interview_agent`, `trend_correlation_agent`, and `cfi_reporter_agent`. CFI `allowed_tools`
contains only the tool surface each agent actually receives: the lead and interview drafter
have none, scope expansion has `sibling_batch_query`, trend correlation has
`cfi_trend_query`, and the reporter has only `cfi_report_write`. Calendar scheduling is a
deterministic post-closure node, not a hidden reporter capability.

`GET /fleet/status` (`app/domain/fleet_registry_status.py`) layers a live Agent Registry
read-back onto the same static manifest: it reports whether the department MCP server
(`COUNTERSIGN_MCP_URL`) and the sponsor A2A agent card (`COUNTERSIGN_SPONSOR_A2A_CARD_URL`)
are currently cataloged in Agent Registry, using the same
`google.adk.integrations.agent_registry.AgentRegistry` client already proven against this
project in `infra/geap/verify_agent_registry.py` — no second, hand-rolled catalog. A Registry
outage or missing configuration is reported honestly as `unreachable`/`not_configured` in the
response body (never a 5xx, never a fabricated `registered`); every manifest agent still
appears so the fleet view does not shrink to empty on a transient platform issue. There is
deliberately no per-agent "last invocation" timestamp here: no durable per-agent invocation
log exists in this codebase, and inventing one would be exactly the kind of fabricated status
this build avoids.

### P1 — real department systems (live)

QC lab and environmental monitoring are now served by real, independently provisioned systems
without changing `qc_lab_agent`/`environmental_monitoring_agent`'s tool contract or the
deterministic `check_lims`/`check_em` that evaluates their results:

- `countersign_lims.lims_results` is live on Cloud SQL. The department MCP service account has
  `SELECT` only; `lims_query` for `aurex`/`AX-2291` returned `system=cloud_sql` and the seeded
  assay result through an authenticated live MCP call.
- `countersign_em.em_readings` is live on BigQuery. The MCP account has `Data Viewer` and
  `Job User` only; `em_query` for the same batch returned `system=bigquery` and the real stored
  environmental-monitoring reading.
- Deployment remains explicit: `COUNTERSIGN_LIMS_BACKEND=cloud_sql` and
  `COUNTERSIGN_EM_BACKEND=bigquery` are set on the live department MCP service. Unset keeps a
  department on the synthetic catalog, so a partially provisioned environment fails safely.
- `infra/geap/provision_department_systems.sh` creates both source systems and the required
  `adk_sessions.case_status` table. The latter is a narrow, structured sponsor-status index;
  it is not an inference over free-text Case Memory.
- Supplier CoA remains an untrusted source handled through the existing evidence path; QMS
  write-back is the next integration work, not implied by this deployment.

All 8 agents in the target table are real, plus `qa_qms_write_agent` (doc `12` T1-1): the 9th
fleet member and the only agent permitted to mutate an external record, running as the graph's
last node (`file_qms_report`) after `record_case_memory`. It files the investigation report and
Annex 16 package as a real Google Doc via its own `qms_report_write` MCP tool, on its own
Gateway allow-list entry disjoint from every read-only tool set. Requires
`COUNTERSIGN_QMS_REPORTS_FOLDER_ID` (the `03 Deviation Reports` folder id) on the department MCP
deployment, and Editor (not read-only) access on that folder for the MCP service account — the
one Drive grant in this system that is a write, not a read.

**Prior-deviation similarity on real BigQuery `VECTOR_SEARCH` (doc `12` T1-4).** The
`deviation_investigator` agent's `prior_art_query` tool can now be backed by a real BigQuery
table instead of Firestore, selected by `COUNTERSIGN_CASE_MEMORY_BACKEND=bigquery` on the
department MCP deployment (unset keeps Firestore; both implement the same `CaseMemoryStore`
protocol, so the MCP tool signatures, the Runtime client, and the Investigation agent's
citation flow do not change). `app/domain/case_memory_store_bigquery.py`:

- Tenant and redaction predicates are placed **inside** the `VECTOR_SEARCH` base-table
  subquery (a pre-filter), never appended as a `WHERE` clause around the function call (a
  post-filter) — per BigQuery's own filtering guidance, a post-filter can silently return
  fewer than `top_k` rows or let a different tenant's closer vectors starve the requested
  tenant's own results out of the candidate set entirely. `tenant_id` and `redacted` are
  declared `STORING` columns on the vector index so this pre-filter can use the index.
- Writes are an idempotent `MERGE` keyed by `fact_id`; redaction is a targeted `UPDATE`, never
  a physical delete — the same rerun-safe, audit-preserving contract the Firestore store
  already has.
- Embeddings use Vertex's task-type parameter (`RETRIEVAL_DOCUMENT` for stored facts,
  `RETRIEVAL_QUERY` for incoming queries, per
  [Vertex's own asymmetric-retrieval guidance](https://cloud.google.com/vertex-ai/generative-ai/docs/embeddings/task-types))
  and a dimension check on every embedding call, applied to the Firestore path too. An
  embedding failure fails the write outright rather than storing a fact with no vector,
  which would be permanently unreachable by similarity with no visible symptom.
- `similarity_score` (real `1 - cosine_distance` from the query result) and
  `root_cause_category` (the investigation draft's actual leading hypothesis category, if one
  was drafted) now flow through `CaseMemoryFact` into the investigation brief — never
  fabricated; a case with no drafted hypothesis reports `None`, not a guessed category.
  `similarity_score` is explicitly documented to the investigation agent as a ranking signal
  only, never evidence of shared root cause.
- `infra/geap/provision_case_memory_bigquery.sh` provisions the dataset/table/IAM;
  `infra/geap/verify_case_memory_bigquery.py` is a live probe proving real cross-tenant
  isolation (a foreign-tenant row with near-identical wording must never leak into the
  requesting tenant's results) and redaction. Fleet manifest reports whichever backend is
  actually configured (`firestore_case_memory` / `bigquery` / `in_memory_fixture`) instead of a
  hardcoded string.
- Not yet done: provisioning the vector index in a live project and flipping the backend flag
  on a deployed MCP service — the scripts exist, the switch has not been made live.

### T1-5 — controlled SOP and public regulatory-guidance RAG

T1-5 is implemented with Vertex AI RAG Engine behind the existing department MCP credential
boundary. It deliberately uses two corpora: a tenant-scoped controlled-SOP corpus and a
separate public regulatory-guidance corpus for Annex 1, ICH Q9(R1), and OOS material. Public
guidance is advisory and can never satisfy the controlled-SOP citation requirement or become
batch evidence.

`controlled_guidance_query` is the only Runtime retrieval path. Vertex results are checked
against the change-controlled `app/domain/master_data/guidance_manifest_v1.json` before they
are returned. The manifest is authoritative for exact `source_uri`, version, approval state,
effective date, supersession state, and tenant scope. Controlled-SOP retrieval applies a
Vertex CEL `tenant_id` filter. The deterministic investigation gate requires at least one
in-force controlled-SOP citation whenever the SOP corpus is configured. Transport outages may
produce an empty advisory result; implementation errors are not swallowed, and production has
no dummy, stale, or silent fallback.

**Manual live provisioning — not performed here.** Before this can be called live, an operator
must:

1. Run `infra/geap/provision_guidance_rag.sh` to create both Vertex RAG corpora and grant
   `roles/aiplatform.user` to the department MCP service account.
2. Grant the Google-provided Agent Platform RAG Data Service Agent Viewer access to the
   controlled SOP Drive folder.
3. Import approved tenant SOPs from the tenant Drive QMS folder into the controlled-SOP corpus.
4. Import the real Annex 1 / ICH Q9(R1) / OOS PDFs into the public-guidance corpus, normally
   from approved Cloud Storage.
5. Add the correct `tenant_id` metadata to every controlled-SOP import for CEL filtering.
6. Set `COUNTERSIGN_GUIDANCE_SOP_CORPUS` and `COUNTERSIGN_GUIDANCE_PUBLIC_CORPUS` on the
   department MCP Cloud Run service; never set `COUNTERSIGN_GUIDANCE_INMEMORY` in production.
7. Add every imported file's exact Vertex-reported `source_uri`, version, approval state,
   effective date, supersession state, and tenant scope to the guidance manifest under change
   control.
8. Redeploy the department MCP service, update/verify the Gateway allow-list, and run an
   authenticated live verification of tenant filtering, source verification, and citation
   gating. No deployment or live verification is claimed yet.

Local validation: `uv run pytest tests/unit/test_guidance_rag.py -q` passed 17 tests;
`uv run pytest tests/unit -q` passed 229 tests with 3 pre-existing `GEMINI_API_KEY` failures
in unrelated case-graph paths; Ruff and `ty check` passed on the touched/new guidance code.

The investigation↔challenger revision loop (a back-edge, per the official
[graph routes doc](https://google.github.io/adk-docs/graphs/routes/)) is a deliberate open
decision, not an oversight — it would change the current, documented rule that challenger
attacks are recorded but never auto-fail the gate.

### T1-7 — public EudraGMDP and openFDA supplier/defect screening

T1-7 is implemented but deliberately **not deployed or claimed live**. The supply-assurance
agent has three exact zero-argument closures: `fetch_supplier_coa`, `eudragmdp_screen`, and
`openfda_enforcement_screen`. The CoA closure captures the typed source record first; each
regulator closure maps only explicitly public manufacturer/site/product/active-ingredient/
defect fields into the dedicated MCP boundary. Tenant, case, batch, internal supplier, lot,
and confidential deviation identifiers never enter a public request.

The regulator clients use only the official EudraGMDP non-compliance portal and official openFDA
drug-enforcement API. Responses are schema-validated and provenance-stamped with the request,
HTTP status, retrieval time, source update time where available, cache state, response hash,
and parser/schema versions. The cache is bounded and TTL-limited; expired entries and
unavailable results are never returned as fallback data. Missing public identity, MCP errors,
upstream transport/HTTP/rate-limit/size/schema failures, and incomplete agent tool calls become
explicit `screening_unavailable` results. Current catalog CoA rows do not contain public
manufacturer/product/defect identity, so current catalog cases produce unavailable results
rather than fabricated queries or broad searches.

`RegulatoryScreeningBundle` and `ScreeningAdvisoryFinding` remain separate from
`ComplianceFinding`. A hit, no-hit, or unavailable result is visible in the case/QMS report as
advisory context only; the Annex 16/release-gate path evaluates CoA compliance findings, not
public-regulator results. Upstream record identifiers remain in typed/report data and are not
returned to the supply model as instructions or free text.

The dedicated private service artifacts are `Dockerfile.regulatory-screening-mcp`,
`infra/geap/cloudbuild-regulatory-screening-mcp.yaml`,
`infra/geap/deploy_regulatory_screening_mcp.sh`, and
`infra/geap/mcp-case-workflow-regulatory-screening-allow.yaml`. Manual configuration still
requires provisioning the two least-privilege service accounts, deploying the private MCP,
applying the Gateway policy/binding checklist and Model Armor host exclusion, granting the
case Runtime Agent Identity token-creator access to the separate screening invoker account,
and passing `--regulatory-mcp-url` plus
`--regulatory-mcp-invoker-service-account` to `deploy_case_runtime.py`. This implementation
session performed none of those deployment/IAM operations.

Local validation after the T1-7 wiring included targeted Ruff and `ty` checks, strict boundary
smokes through the in-process MCP, official live EudraGMDP/openFDA client checks, and
`uv run pytest tests/unit -q`: 229 tests passed; three unrelated pre-existing
`test_case_graph.py` fixture failures remain because that fixture invokes an unstubbed EM LLM
path and patches a lookup symbol that is not exposed by `department_tools.py`.

A follow-up design review (via the `semantic_reviewer` sub-agent) against the working diff
found and fixed three implementation gaps before anything else was considered done: the supply
agent's own instruction text named a tool (`screen_eudragmdp`) that did not match the exposed
`eudragmdp_screen`/`openfda_enforcement_screen` tool constants; openFDA HTTP redirects were
followed without validating the final host/path/query against the official endpoint (the
EudraGMDP client already had this check); and the branch-side `_screening_result_contract`
accepted any result sharing the expected URL's scheme/host/path rather than checking the exact
EudraGMDP request parameters, exact openFDA request URL/parameters, parser/schema version, and
cache state. All three are fixed: the instruction now interpolates the real tool-name constants,
an `_OpenFdaRedirectHandler`/`_validated_openfda_url` pair pins openFDA redirects the same way
Eudra redirects are pinned, and the contract check now compares the complete request contract.
`deploy_regulatory_screening_mcp.sh` is also now executable. Re-run after these fixes: Ruff and
`ty` clean on every touched file, `uv run pytest tests/unit -q` unchanged at 229 passed / the
same three pre-existing failures, and both a live EudraGMDP no-hit and a live openFDA hit (plus
its cached replay) pass the tightened contract check.

Findings from that same review left unchanged, because they describe known, already-documented
limitations rather than defects: the synthetic catalog CoA carries no public manufacturer/
product/defect identity, so current catalog cases correctly resolve to `screening_unavailable`
rather than a fabricated query; and the private Cloud Run deployment, Gateway policy
application, and Runtime IAM grants remain manual, unapplied steps, exactly as stated below.

### P3 — sponsor liaison A2A peer (agent 8 of 8)

`app/sponsor_liaison/` — a real, separate FastAPI/A2A service, not a `Workflow` node, per doc
10 §4.1's "A2A peer, separate service." Answers a sponsor's own QA agent one scoped question:
the coarse status of one of their own batches.

- Real agent with one tool (`get_batch_status`), bound as a closure locked to the
  already-authenticated caller's `sponsor_id` — the model supplies only `batch_id`, never
  `sponsor_id`, so a prompt-injected instruction can change which batch is asked about but
  never which company is asking.
- Authentication is a per-sponsor bearer API key
  (`COUNTERSIGN_SPONSOR_API_KEYS=key:sponsor_id,...`), issued out of band at Quality Agreement
  onboarding — sponsor identity mechanisms (Agent Identity SPIFFE, OIDC audience allow-lists)
  assume the caller is provisioned inside this project's IAM, which a different company's
  agent is not.
- The scope check (`COUNTERSIGN_SPONSOR_TENANT_MAP=sponsor_id:tenant_id,...`) runs inside the
  `sponsor_batch_status` MCP tool itself, before the store is touched, so a denied request and
  a not-found batch are not distinguishable by response shape or latency. No default map —
  unset means no sponsor is authorized for anything.
- New Cloud SQL `case_status` table (same instance as sessions/sterility), written by
  `sterility_wait.py`/`qp_confirmation.py` at the two points the case workflow already knows the
  real lifecycle fact, read only by the sponsor liaison. `SponsorBatchLifecycleView` only has
  values this slice can actually prove (`on_sterility_hold`, `qp_confirmed`, `qp_declined`,
  `qp_could_not_confirm`, `not_found`) — no invented "under investigation" stage the store
  cannot observe.
- New Gateway policy (`infra/geap/mcp-sponsor-liaison-status-only.yaml`): the `sponsor_liaison`
  identity may call `sponsor_batch_status` only, with explicit `forbidden_bindings` in both
  directions against every other identity.
- **Live proof (2026-08-23):** deployed as
  `countersign-sponsor-liaison` on Cloud Run; its agent card is public while every request is
  authenticated in-app with a per-sponsor API key. An authorized `aurex` call for `AX-2291`
  returned `on_sterility_hold` and the real due date; an `othercompany` tenant request was
  denied; an absent batch returned `not_found`; no key or a wrong key returned HTTP 401.
  The live department MCP call was separately verified to return the same structured status.

### Domain workflow — deterministic intake and classification

The first node of the GMP deviation-investigation graph (`final_reseach/ARCHITECTURE.md`
§7 / §12) is implemented in `app/domain/`:

- `models.py` — `CaseIntake` (structured detection payload) and `ClassifiedCase`
  (`tenant_id`, `lifecycle_state`, `decision_authority: "none"` on every record), following
  Veeva's Agent Card vocabulary (`final_reseach/02_veeva_vault_qms.md` §2)
- `classification.py` — pure-function minor/major/critical severity rules over structured
  signals (`patient_safety_impact`, `sterility_assurance_impact`, etc.), no model call, per the
  Annex 22 draft posture that the LLM is never in the critical decision path
- `intake.py` — `classify_intake` is shared by the standalone intake node and the joined
  case graph. The case graph stamps tenant / case / batch onto the BMR ingest request and
  stores the classified case in workflow state for the department join.

Verified against installed `google-adk` 2.6.3 source directly (`workflow/_function_node.py`),
not assumed from documentation. 14 unit tests in `tests/unit/test_classification.py` and
`tests/unit/test_intake.py`, run through a real `Workflow` + `InMemoryRunner`, not just the
bare function.

### Domain workflow — multimodal evidence extraction

The second stage (`final_reseach/ARCHITECTURE.md` §7) is implemented as a static
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

### Real external system: manufacturing evidence over Google Drive

Proven live (2026-08-19), not a synthetic catalog row like the QC/supply connectors below:

- `drive_fetch_evidence` MCP tool (`app/mcp/department_server.py`) reads one file by id
  through the Drive API v3, base64-encoded, ready for `decode_and_verify_payload`/
  `ingest_evidence_artifact` unchanged. Runs inside the MCP process, which holds the Drive
  credential — an Agent Identity principal cannot itself be a Drive OAuth/service-account
  principal, the same constraint that put the sterility clock and Case Memory behind MCP.
- `collect_manufacturing` (`app/domain/branches.py`) pulls the real page by `drive_file_id`
  before ingest instead of trusting bytes pushed in at case-open time — the same pull pattern
  QC and supply already use for LIMS/EM/CoA, rather than a special case for manufacturing.
- Same MIME allow-list and size ceiling as the ingest boundary
  (`app/domain/evidence_models.py`); Google-native formats (Docs/Sheets/Slides) are rejected
  rather than silently exported to PDF, since an exported copy is not the artifact the case
  would be citing.
- The web open-intake form requires a real Drive file id for a live case — there is no
  placeholder evidence on the live path; `POST /api/cases` returns `400` if it is missing.
- Verified live end to end: a synthetic labelled scanned BMR page, uploaded to a Drive folder
  shared read-only with the department MCP service account, was fetched by a real case run on
  the deployed Agent Runtime, and the extractor read its actual field values off it (batch
  number, product code, etc., all ≥0.99 confidence) before the case reached the sterility hold.
- The Drive folder is organized as a QMS-style tree (`Countersign QMS/01 Batch Manufacturing
  Records/`, `02 Standard Operating Procedures/`, `03 Deviation Reports/`, `04 Certificates of
  Analysis (CoA)/`, `05 Environmental Monitoring Logs/`, `06 Sterility Assurance Records/`),
  shared read-only at the top folder only — new files placed anywhere under it are covered
  without re-sharing.
- The manual "paste a Drive file id" input on `/open` was replaced with the real Google Drive
  Picker (`web/src/lib/drive-picker.ts`): the operator clicks "Choose from Drive," signs in
  with their own Google account (browser-side OAuth, `drive.readonly` scope), and picks a file
  from a native Drive browser instead of copying an id. The picked file's id flows into the
  same `drive_file_id` field the manual input used, so no change was needed downstream
  (`live-store.ts`, `POST /api/cases`, `drive_fetch_evidence`). Requires
  `NEXT_PUBLIC_GOOGLE_CLIENT_ID` (OAuth 2.0 Web application client) and
  `NEXT_PUBLIC_GOOGLE_API_KEY` (browser key restricted to the Drive API + Picker API) in
  `web/.env.local`. Verified live: picked the same BMR page from the new folder structure
  through the picker UI and reproduced the identical result (manufacturing `OK`, real Drive
  `webViewLink` cited in compliance findings, case reached the sterility hold) — see
  `web/test-data/2.case-aurex-drive-test-01.md`.

### Domain workflow — deterministic GMP compliance engine

The third stage (`final_reseach/ARCHITECTURE.md` §7, "Deterministic compliance engine")
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
  (`final_reseach/01_katalyze_ai.md` §3). This is a tag, not a determination: it hands the
  Investigation agent a starting hypothesis dimension, never a root cause.

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

The manufacturing BMR path still runs this engine. Parallel QC/supply checks and the joined
case graph are described below.

### Domain workflow — joined case graph (intake → departments → QP)

`app/agent.py` serves `build_deviation_case_workflow()` as `root_agent` with
`ResumabilityConfig(is_resumable=True)` so sterility hold and QP prompts continue
by `invocation_id`. Set `SESSION_SERVICE_URI` to `postgresql+asyncpg://…` for
`DatabaseSessionService` (visible SQL rows). Vertex sessions are not used when
that URI is set. The GEAP hello agent used for Gateway deny proof stays under
`infra/geap/hello_runtime` and is not this graph.

```
START → intake_and_bind_evidence
      → (collect_manufacturing, collect_qc, collect_supply)
      → join_departments
      → reconcile_departments
      → draft_investigation
      → challenge_investigation
      → gate_investigation
      → await_sterility_incubation
      → evaluate_annex16_release_gate
      → confirm_qp
```

- Intake classifies the deviation, persists `classified_case` in workflow state, and stamps
  tenant / case / batch onto the BMR ingest request so an attachment cannot claim a different
  sponsor than the case. Unstructured chat fails closed at `DeviationCaseRequest` schema.
- Manufacturing runs ingest → extract → validate → compliance. `ctx.run_node` results are
  re-validated as Pydantic models (ADK returns dicts). Disallowed MIME types fail that
  branch with the integrity message; QC and supply still join. Reconcile refuses a
  manufacturing result whose tenant / case / batch does not match the classified case.
- QC and supply are **code**, not extra LLM agents. They call named MCP tools
  `lims_query`, `em_query`, and `coa_fetch` over the MCP protocol. Runtime must
  set `COUNTERSIGN_MCP_URL` (https Streamable HTTP) so egress hits Agent Gateway;
  omitting it fails the branch. Unit tests set `COUNTERSIGN_MCP_INPROCESS=1`.
  Tool bodies read connector-shaped LIMS / EM / CoA rows from
  `master_data/department_catalog_v1.json`; OOS, action-limit, signature, lot,
  retest, and GMP-certificate checks stay deterministic after the fetch.
  Investigation still has `tools=[]`. MES, historian, and QMS are not registered.
  Live Cloud SQL / BigQuery / Drive are not wired.
- Each collector always emits `Event.output` so a failed branch cannot stall `JoinNode`.
- Investigation and Challenger are `single_turn`, `include_contents="none"`, `tools=[]`,
  `output_schema` bound. Scope ids are stamped from the classified case. Investigation
  runs when every department returned data, including when OOS findings exist — those
  findings are the brief, not a reason to skip.
- The citation gate is code: unknown `source_uri` / `finding_id` blocks. Challenger attacks
  stay on the package for the QP and cannot veto a cited draft. `decision_authority` stays
  `"none"`. This is not a batch disposition.
- Sterility incubation is external store state, not an LLM and not
  `LongRunningFunctionTool`. `await_sterility_incubation` (`rerun_on_resume=True`)
  starts a per-batch clock (14 days, or `COUNTERSIGN_STERILITY_HOLD_SECONDS` labelled
  as compressed) and checkpoints with `RequestInput`. The store is authoritative: a
  scheduler tick that does not mark the row complete does not advance.
  An Agent Identity SPIFFE principal **cannot** be a Cloud SQL IAM database
  user — confirmed against the live instance (`gcloud sql users create
  <effective_identity>` rejects the name) — so the workflow reaches the clock
  through named MCP tools (`sterility_start`, `sterility_get`,
  `sterility_bind_resume_handle`) on the same department MCP service as
  LIMS/EM/CoA, not by opening Cloud SQL itself. Those tools require
  `case_tenant_id` to match the tenant they are reading, the same
  cross-tenant check the department reads already enforce (the TQA callout
  only inspects `lims_query`/`em_query`/`coa_fetch`, not these). The MCP
  server still owns Cloud SQL via the Python Connector and IAM auth
  (`DB_IAM_USER`, no default admin user). At checkpoint time the node binds a
  `SterilityResumeHandle` (`app_name`, `user_id`, `session_id`,
  `invocation_id`, `interrupt_id`) onto the row so a scheduler tick weeks
  later can resume the exact paused invocation. `tick_due` is an atomic
  `UPDATE ... RETURNING` claim in every store, so two overlapping ticks
  cannot both claim and deliver the same wake-up. Proven live:
  `countersign-sessions` (`POSTGRES_16`, `adk_sessions`) — IAM write to
  `sterility_incubation` and ADK session round-trip; tables `sessions`,
  `events`, `sterility_incubation`.
- `POST /scheduler/sterility-tick` (`app/scheduler/`) is the scheduled wake-up:
  OIDC-guarded (audience + service-account allow-list; fails closed if either
  is unset), completes due holds, and resumes each one via the in-process ADK
  `Runner` or, when `COUNTERSIGN_CASE_ENGINE` is set, Agent Runtime
  `:streamQuery` with the recorded `invocation_id`. A resume failure records
  `recoverable: false` when the session/invocation itself is gone
  (`SessionNotFoundError`, HTTP 404/400) versus `true` for a transient error,
  so an operator can tell which completed holds actually need attention. A
  hold that completes with no bound handle is reported, not silently
  swallowed. Proven end to end in `tests/unit/test_sterility_wait.py`: a case
  parked on the hold advances to `qp_confirmation` from a tick alone, with no
  direct call into the workflow.
- `infra/geap/deploy_case_runtime.py` deploys the real graph (`app.agent`) to
  Agent Runtime with `identity_type=AGENT_IDENTITY` and
  `agent_gateway_config`, following the same identity-assert and IAM-grant
  pattern as the hello probe (`infra/geap/runtime_identity.py`). Because the
  Runtime identity cannot hold Cloud SQL credentials, this deploy sets
  `COUNTERSIGN_ALLOW_VERTEX_SESSIONS=1` and `COUNTERSIGN_MCP_URL` instead of
  `INSTANCE_CONNECTION_NAME`/`DB_IAM_USER` (`GOOGLE_CLOUD_AGENT_ENGINE_LOCATION`
  and `GOOGLE_CLOUD_PROJECT` are now platform-reserved and injected
  automatically, not set by this script). Run live against
  `countersign-505107`: reasoningEngine `4625084117367652352`, pinned to
  Python 3.12 (the platform's default build image is 3.10, which lacks
  `enum.StrEnum`). Sterility hold + scheduler resume proven live end to end
  (2026-08-18): a disposable case parked on a compressed hold, the real
  `/scheduler/sterility-tick` endpoint resumed the exact `invocation_id` it
  parked on, and an unrelated due case was left untouched. Not claimed: a
  live Gateway 403 from this deployed Runtime's own MCP egress; Agent
  Runtime does not expose Cloud Run revisions to kill, so revision-kill is
  still proven only in `tests/unit/test_kill_resume.py`, against an
  in-process `Runner`.
- The Annex 16 node is also code. Checklist items live in hashed master data
  (`master_data/annex16_checklist_v1.json`). Bound evaluators score only the matching
  department branch: manufacturing completeness (1.7.11), QC LIMS OOS (1.7.13), CoA
  gating (1.7.6). A supply completeness finding does not fail 1.7.11. Open-deviation
  items 1.7.16 and 1.7.17 fail while lifecycle is `CLASSIFIED`. 1.7.18 stays
  `not_evaluated` until a pack-level TQA evaluator is bound (the gateway TQA
  extension is separate and already live). Required unevaluated or failed items
  make the pack `not_ready`. Outcomes are `ready_for_qp_review` / `not_ready` — never
  certified or released. This is a CDMO confirmation pack, not MAH certification.
- QP sign-off is a graph `RequestInput` pause on `confirm_qp` with
  `rerun_on_resume=True` (ADK `request_input_rerun` pattern). The pack is recovered
  as `node_input` from the event log, not from session state. Allowed replies are
  `confirm`, `decline`, and `cannot_confirm`. `confirm` while the pack is `not_ready` is
  recorded as `rejected` and does not flip the gate. Machine `decision_authority` stays
  `"none"`. There is no UI yet; resume is by `FunctionResponse` on the same invocation.

Standalone `evidence_graph.py` (ingest → extract → validate → comply) remains for the
single-artifact path. The platform entrypoint is the joined case graph.

### Domain workflow — Case Memory (Firestore + embeddings)

`retrieve_prior_art` runs after `reconcile_departments` and before Investigation;
`record_case_memory` runs after `confirm_qp`. Neither is an LLM and neither is on the
citation gate's critical path — a Case Memory outage degrades to an empty `prior_art`
list or a skipped write, not a failed case.

- **Not `VertexAiMemoryBankService`.** `search_memory` drops `custom_metadata`/`id` on
  read (provenance cannot round-trip), scopes only by `app_name`/`user_id` (no tenant
  filter), has no delete method (a poisoned fact cannot be redacted), and writes are
  fire-and-forget. `CaseMemoryFact` (`app/domain/case_memory_models.py`) carries
  `fact_id`, `source_uri`, `tenant_id` and `confidence` as first-class fields; every
  write is `await`ed synchronously; `redact()` exists on both backends and is
  tenant-scoped.
- **Retrieval is semantic, not keyword.** `text-embedding-005` via
  `google.genai`'s `embed_content` (not a separate embeddings SDK — already a
  transitive dependency through `google-adk`), then Firestore's native
  `find_nearest` KNN query (`Vector`, `DistanceMeasure.COSINE`). If embeddings are
  unavailable, retrieval falls back to a tenant-scoped recency query and reports
  `query_embedded: false` — "no similar priors" is never confused with "search
  degraded."
- **Reached over MCP, not opened directly.** Same reason as the sterility clock: an
  Agent Identity principal cannot itself hold Firestore IAM credentials. Three new
  tools on the department MCP process — `prior_art_query`, `prior_art_write`,
  `prior_art_redact` — mirror the existing `lims_query`/`sterility_*` pattern (tenant
  self-check, `ToolError` wrapping, read-only vs idempotent-write annotations).
  `prior_art_redact` is deliberately **not** on the case-workflow Gateway allow-list —
  redaction is an operator decision about a fact's correctness, not something a case
  should trigger on its own behalf — and has its own CLI entry point,
  `countersign-case-memory-redact` (`app/domain/case_memory_redact.py`).
- **The one durable write is idempotent.** `record_case_memory` is
  `rerun_on_resume=True`, so a crash between `confirm_qp` completing and this write
  landing, followed by an ADK resume, reruns the node. The fact document id is
  deterministic (`qp_outcome:{tenant_id}:{case_id}`), not a fresh `uuid4` per call, so
  a rerun overwrites the same fact instead of duplicating it — the same idempotency-key
  property `sterility_start` already has.
- **Store-outage exceptions are narrow, on purpose.** Both nodes catch only the actual
  failure modes an outage can raise (`DepartmentToolCallError`, `McpError`,
  `GoogleAPICallError`, `RetryError`, `ConnectionError`, `TimeoutError`) — not bare
  `Exception` — so a real bug in this code path fails loudly instead of masquerading as
  Case Memory being down.
- Reviewed via `semantic_reviewer` sub-agent before commit. Verdict: needs changes; all
  findings fixed before commit — the write-idempotency and narrow-exception points
  above are exactly what that review caught, plus removing two unused request models
  and adding the redact CLI so the tool has an actual caller.
- **Live store is proven.** `infra/geap/provision_case_memory.sh` created Native
  `(default)` in `us-central1`. The vector composite index is `tenant_id` +
  `redacted` + `embedding` (768, `text-embedding-005`) because `query()` pre-filters
  both fields before `find_nearest` — a vector-only index would not serve that
  query ([pre-filter requires a composite vector index](https://cloud.google.com/firestore/native/docs/vector-search)).
  Recency fallback has its own composite (`tenant_id`, `redacted`, `observed_at DESC`).
  Both indexes are `READY`. The department MCP SA holds `roles/datastore.user` and
  `roles/aiplatform.user`. `infra/geap/verify_case_memory.py` wrote two probe facts,
  ran an embedded KNN query that returned only the matching tenant, redacted, and
  deleted the probes. Unit tests still use `InMemoryCaseMemoryStore`.
- **Department MCP Cloud Run serves Case Memory.** Redeployed
  `countersign-department-mcp` with Vertex + Firestore env.
  `verify_mcp_case_memory.py` listed `prior_art_*`, wrote and queried over
  Streamable HTTP `/mcp` with a user identity token (Cloud Run `--audiences`
  requires a service account; the [developer invoke path](https://docs.cloud.google.com/run/docs/authenticating/developers)
  uses a user token). Result: `query_embedded=true`.
  **Not claimed:** a live Runtime case that retrieved prior art through Agent
  Gateway; a Gateway 403 from that Runtime's MCP egress.

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
updated to `gemini-3.5-flash-lite` to match the rest of the fleet; this transcript is left verbatim as
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

Note `gemini-3.5-flash-lite` resolves via the **global** Vertex hostnames. `GOOGLE_CLOUD_LOCATION`
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
    evidence_graph.py                   standalone ingest → extract → validate → comply graph
    case_graph.py                       joined deviation-case Workflow (platform entrypoint)
    case_models.py                      DeviationCaseRequest / CaseComplianceResult
    department_models.py                LIMS / EM / CoA snapshots + branch outcomes
    department_catalog.py               pinned synthetic department catalog loader
    department_tools.py                 named LIMS / EM / CoA tool bodies
    department_checks.py                deterministic QC and supply checks
    branches.py                         parallel collectors (always Event.output)
    investigation.py                    draft-only investigation agent (no tools)
    investigation_brief.py              citation-constrained brief for both LLM nodes
    investigation_models.py             draft / challenger / gate schemas
    investigation_gate.py               deterministic citation / readiness gate
    sterility_models.py                 incubation record / resume-handle / tick types
    sterility_store.py                  in-memory or sqlite incubation table (atomic tick_due)
    sterility_store_postgres.py         Cloud SQL incubation table (IAM connector, atomic tick_due)
    sterility_store_mcp.py              async client the case Runtime uses (no direct Cloud SQL)
    sterility_wait.py                   RequestInput checkpoint; store is authoritative
    sterility_tick.py                   mark due incubations complete (operator CLI)
    case_memory_models.py               CaseMemoryFact / query result types
    case_memory_store.py                Firestore (find_nearest) or in-memory Case Memory store
    case_memory_store_bigquery.py       BigQuery VECTOR_SEARCH Case Memory store (doc 12 T1-4)
    case_memory_mcp.py                  async client the case Runtime uses (no direct Firestore)
    case_memory_node.py                 retrieve_prior_art / record_case_memory graph nodes
    case_memory_redact.py               redact one poisoned fact (operator CLI)
    guidance_models.py                  controlled SOP/public guidance models and citations
    guidance_manifest.py                change-controlled source and document-control validation
    guidance_source.py                  Vertex AI RAG Engine retrieval adapter
    guidance_mcp.py                     async MCP client for controlled guidance retrieval
    guidance_node.py                    retrieve controlled guidance graph node
    master_data/guidance_manifest_v1.json  controlled guidance source ledger
    workflow_meta.py                    workflow name + version stamped into session state
    challenger.py                       clean-context verifier (no tools)
    release_models.py                   Annex 16 checklist result types
    release_gate.py                     deterministic confirmation checklist
    qp_models.py                        QP confirmation request / record types
    qp_confirmation.py                  RequestInput pause; reruns on resume
    master_data/annex16_checklist_v1.json  hashed Annex 16 §1.7 item bindings
    master_data/department_catalog_v1.json  synthetic LIMS / EM / CoA rows
  mcp/
    department_server.py                FastMCP Streamable HTTP: `lims_query` / `em_query` /
                                           `coa_fetch` / `sterility_start` / `sterility_get` /
                                           `sterility_bind_resume_handle` / `prior_art_query` /
                                           `prior_art_write` / `prior_art_redact` /
                                           `controlled_guidance_query`
    client.py                           collector and guidance MCP clients
  scheduler/
    auth.py                              OIDC verify for Cloud Scheduler (fails closed)
    resume.py                            deliver a tick; Runner or Agent Runtime :streamQuery
    routes.py                            POST /scheduler/sterility-tick
infra/geap/
  constants.env                       shared resource names
  agent-gateway-egress.yaml           Agent-to-Anywhere gateway spec
  runtime_identity.py                 shared identity-assert / IAM-grant helpers
  deploy_case_runtime.py              deploy the real graph to Agent Runtime — run live
  iap-request-authz-extension*.yaml   dry-run and enforced variants
  iap-request-authz-policy.yaml       binds the IAP extension to the gateway
  model-armor-authz-extension.yaml    Model Armor CONTENT_AUTHZ extension
  model-armor-authz-policy.yaml       binds the Model Armor extension to the gateway
  mcp-case-workflow-tools-allow.yaml  native MCP ALLOW: department, sterility and Case
                                         Memory read/write tools (case identity)
  mcp-qc-probe-lims-only.yaml         QC-probe identity: lims_query only
  mcp-tool-policy-bindings.yaml       which identity may receive which policy
  deploy_department_mcp.sh            Cloud Run deploy; refuses unauthenticated
  cloudbuild-department-mcp.yaml      image build for Dockerfile.mcp
  deploy_tick_service.sh              Cloud Run deploy for the scheduler tick service
  provision_tqa_vpc.sh                TQA VPC / NAT / private DNS / PSC attachment
  deploy_tqa_callout.sh               private VM ext_proc on :443
  update_tqa_callout.sh               rebuild image on the existing callout VM
  attach_tqa_gateway.sh               DNS peer + VPC attach + TQA CONTENT_AUTHZ
  verify_tqa_reachability.sh          FQDN TLS check from a VPC probe VM
  verify_tqa_policy.sh                allow/deny probe + gateway networkConfig
  provision_sessions_sql.sh           Cloud SQL Postgres + IAM user + Secret Manager
  provision_case_memory.sh            Firestore Native + vector/recency indexes + MCP IAM
  provision_case_memory_bigquery.sh   BigQuery Case Memory dataset/table/IAM (doc 12 T1-4)
  provision_guidance_rag.sh             Vertex RAG corpora + department MCP IAM for T1-5
  verify_case_memory.py               live KNN + tenant filter + redact probe
  verify_case_memory_bigquery.py      live VECTOR_SEARCH + tenant isolation + redact probe
  verify_mcp_case_memory.py           authenticated Streamable HTTP prior_art_* on Cloud Run
  bootstrap_sessions_sql.py           GRANT IAM user; create sterility_incubation
  verify_sessions_sql.py              live IAM write + ADK session round-trip
  agent-gateway-vpc.yaml              live gateway networkConfig (attachment immutable)
  tqa-authz-extension.yaml            FQDN extension, failOpen false
  tqa-authz-policy.yaml               CONTENT_AUTHZ on MCP tools/call
  tqa_spike/                          tenant + lifecycle ext_proc server
  register_platform_endpoints.sh      registers endpoints + grants roles/iap.egressor
  verify_governance.sh                9-check governance gate
  evidence/                           captured proof from the last verification run
  hello_runtime/                      minimal agent used for the governance proof
Dockerfile.mcp                        Cloud Run image for the department MCP server
web/                                  Next.js 16.3 case desk (queue, case, QP; fixtures)
tests/                                unit, integration, eval
```

### Case desk

Next.js 16.3 App Router in `web/` (`npm run dev` from that directory). Queue, case
review, and structured intake. Labelled-synthetic fixtures (`AX-2291`, `AX-2402`, `HX-104`)
are a local/dev aid only — seeded when `COUNTERSIGN_ENABLE_FIXTURES=true` is set, off by
default, so a production deployment's queue shows only real cases opened through intake or the
deployed Agent Runtime; the `Source` (fixture/intake/live) column is likewise only shown when
fixtures are enabled. Severity on new intake uses the same rules as
`app/domain/classification.py`. The sterility incubation hold is a required field QA sets per
case at intake (`sterility_hold_days`) — there is no platform-wide default, since pharmacopeia
sterility test durations are product-specific; `COUNTERSIGN_STERILITY_HOLD_SECONDS` remains
available only to compress the clock for local/dev testing. QP confirm is refused while the
pack is `not_ready`. Raw internal source URIs (Drive, Cloud SQL, BigQuery, Case Memory, Chat,
fixture references) render a human-readable label by default in the case and CFI panels, with
the exact URI available on demand for audit. The desk does not hold an SSE stream. Live Agent
Runtime `streamQuery` is not proxied yet (`501` if `source: live`). Intake-opened cases are
in-process and reset on restart.

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
- `google-adk` is installed with the `[gcp,otel-gcp,agent-identity]` extras. The
  `agent-identity` extra is required for `google.adk.integrations.agent_registry.AgentRegistry`
  (used by `GET /fleet/status`) — without it, importing that client fails with
  `ModuleNotFoundError: google.cloud.agentidentitycredentials_v1`. Found live during this
  build; fixed by adding the extra rather than working around the import.
- `integrations/agent_registry` is **read-only** — agents are registered there and read back,
  with a separate write path for status changes.
- Custom authorization extensions do **not** validate the callout server's TLS certificate;
  reachability is constrained to a VPC-internal FQDN instead.
- Gateway custom authorization policy budget is 4 per Agent-to-Anywhere gateway. IAP, Model
  Armor, and TQA use 3; 1 remains in reserve.
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
- `app/agent.py` serves the joined deviation-case `Workflow`. The Gateway deny probe remains
  `infra/geap/hello_runtime` so that proof stays isolated from the case graph.
- Department LIMS / EM / CoA rows are **synthetic connector shapes**, not live Cloud SQL /
  BigQuery / Drive calls. Collectors reach them through named MCP tools. Runtime
  without `COUNTERSIGN_MCP_URL` fails closed (tests opt in with
  `COUNTERSIGN_MCP_INPROCESS=1`). Gateway YAML allows all three names on the
  **case Workflow** identity; `lims_query` only on the QC-probe identity. Those
  policies are not bound live yet, so a Gateway 403 is not claimed. Cloud Run
  deploy refuses `--allow-unauthenticated`; MCP POSTs also require a bearer
  token. MES and QMS are not this slice.
- TQA is a `CONTENT_AUTHZ` extension (`countersign-tqa`, `failOpen: false`) on
  `tqa.countersign.internal`, scoped to MCP `tools/call`. The callout allows
  `aurex`/`classified` and denies `tenant_mismatch` and `lifecycle_denied`
  (evidence `infra/geap/evidence/tqa-policy.txt`). `countersign-egress` has
  `networkAttachment` `countersign-tqa-psc` and DNS peering for
  `countersign.internal.` only. The platform does not verify the callout TLS
  certificate. Wrong-department remains native MCP tool-name YAML (not bound
  live). A Runtime MCP 403 through the gateway is not claimed.
- **An Agent Identity SPIFFE principal cannot be a Cloud SQL IAM database
  user** — confirmed against the live instance, not assumed: `gcloud sql
  users create <effective_identity>` rejects the name (too long, and the
  type only accepts a user account, service account, or group). The case
  Runtime therefore never opens Cloud SQL itself. It reaches the sterility
  clock through the same MCP service that serves LIMS/EM/CoA, and uses
  Vertex-managed sessions instead of `DatabaseSessionService`. The TQA
  callout's tenant/lifecycle check only inspects
  `lims_query`/`em_query`/`coa_fetch`, so the sterility MCP tools check
  `tenant_id == case_tenant_id` themselves rather than relying on that
  extension.
- **The scheduler tick and the case-workflow Runtime deploy are live, and the
  hold/resume path is proven end to end.** `POST /scheduler/sterility-tick`
  runs on Cloud Run (`countersign-tick-service`, its own least-privilege
  service account, `--no-allow-unauthenticated`), and Cloud Scheduler job
  `countersign-sterility-tick` calls it on a `*/15 * * * *` OIDC-authenticated
  schedule. `infra/geap/deploy_case_runtime.py` has been run against
  `countersign-505107` (reasoningEngine `4625084117367652352`), granted
  `roles/run.invoker` on the department MCP service and `roles/iap.egressor`
  on its registered Agent Registry endpoint. A disposable case parked on a
  compressed hold, an authenticated call to the real tick endpoint reported
  `{"completed":1,"resumed":1}` with the same `invocation_id` the case
  parked on, the desk's next poll showed it advanced past the hold, and an
  unrelated case with a later `due_at` was left untouched. Revision-kill
  itself is still proven only in `tests/unit/test_kill_resume.py`, against
  an in-process `Runner` — Agent Runtime does not expose a Cloud Run
  revision to delete. A live Gateway 403 from the deployed Runtime's own MCP
  egress (as opposed to the hello probe's) is also not yet
  captured as evidence.
- **Case Memory Firestore and department MCP are live.** Native `(default)` in
  `us-central1`, both composite indexes `READY`, MCP SA granted Firestore + Vertex
  embed IAM. `verify_mcp_case_memory.py` called `prior_art_write`/`prior_art_query`
  on `countersign-department-mcp` with a user identity token (developer path;
  `--audiences` is service-account only) and got `query_embedded=true`. Runtime
  prior-art on a live case and a Gateway 403 from that Runtime are not claimed.

---

## Sources

- `final_reseach/ARCHITECTURE.md` — build source of truth
- `final_reseach/10_status_and_remaining_work.md` — remaining work
- `final_reseach/11_demo_and_submission_plan.md` — demo beats
- [Agent Gateway overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/agent-gateway-overview)
- [Set up Agent Gateway](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/set-up-agent-gateway)
- [Delegate authorization with Service Extensions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/delegate-authorization)
- [Route Agent Runtime traffic through Agent Gateway](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-gateway-runtime-deploy)
- [Troubleshoot Agent Gateway connectivity](https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/troubleshoot-agent-gateway)
- [Authenticate using an agent's own authority](https://docs.cloud.google.com/iam/docs/auth-agent-own-identity)
- [EU GMP Annex 22 draft (EC consultation)](https://health.ec.europa.eu/document/download/5f38a92d-bb8e-4264-8898-ea076e926db6_en)
- [FDA Warning Letter 320-26-58](https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/warning-letters/purolea-cosmetics-lab-722591-04022026)

## P2 — Agent Registry catalog publication and read-back proof (2026-08-23)

The live catalog is published and verified with `infra/geap/verify_agent_registry.py`:

```bash
COUNTERSIGN_MCP_INVOKER_SERVICE_ACCOUNT=countersign-case-invoker@countersign-505107.iam.gserviceaccount.com \
  uv run python infra/geap/verify_agent_registry.py
```

The read-only verifier fails closed unless all of these match the live systems:

- The case Workflow Runtime reasoning engine `4826409094459949056` is present as the
  auto-registered `countersign-case` Agent Registry agent.
- `countersign-sponsor-liaison` is registered from its real A2A agent card, with
  `sponsor_liaison_agent`, skill `sponsor_batch_status`, and the public Cloud Run A2A URL.
  The verifier fetches the public card again rather than trusting only Registry output.
- `countersign-department-mcp-tools` is registered as an MCP `TOOL_SPEC` using the live
  department MCP URL and its tool names exactly match a fresh authenticated `tools/list`
  response. The current catalog contains 12 tools.
- `app/domain/fleet_manifest.py` builds exactly 8 real fleet entries, including the separate
  `sponsor_liaison_agent` A2A peer.

The existing endpoint-only `countersign-department-mcp` Registry service was preserved because
Registry does not permit changing a service's spec oneof. The graph agents are not claimed as
eight separately deployed Registry resources: they run inside the one case Runtime and are
represented by the live fleet manifest; the sponsor liaison is the separately deployed A2A peer.

## P4 — Agent Observability on a live case (2026-08-23)

`infra/geap/verify_observability.py` proves Agent Observability tracing on the deployed case
Runtime, not just its configuration:

```bash
uv run python infra/geap/verify_observability.py
```

It sends one bounded canary through `:streamQuery`, captures only the returned invocation id
and timestamps, then queries Cloud Trace for exactly one matching `invoke_workflow` span for
that exact Runtime resource inside a narrow time window, failing closed on zero or ambiguous
matches. While building it, Cloud Trace v1's `traces.list` pagination was found to return a
near-empty first page even when the canary's own trace exists later in the result set; the
verifier now follows pagination fully rather than reading page one.

**Stated honestly:** the exported span for `invoke_workflow deviation_case` carries Runtime,
workflow, and service metadata but no `invocation_id` attribute, so correlation is by exact
Runtime resource plus a bounded time window, not by invocation id. The verifier's own output
says this rather than claiming a correlation the platform does not currently expose.

A real live case (`aurex`/`P4-OBS-01`, reusing the same real Google Drive BMR page already
proven live for P1) was run end to end on the deployed Runtime and reached the sterility hold.
Its Cloud Trace export (saved at `infra/geap/evidence/p4_live_case_trace.json`) contains 67
spans: one `invoke_node` span per graph node that actually ran — intake, all department
collectors, the join, reconcile, prior-art retrieval, investigation draft, challenger, citation
gate, and the sterility-hold checkpoint — plus 26 MCP tool-call spans and Model Armor
`apply_guardrail` spans on the same request. This is the real fleet topology as Agent
Observability recorded it for one live invocation.

**Stated honestly:** that trace has no `collect_environmental_monitoring` span. The currently
deployed case Runtime predates the environmental-monitoring agent split (`ba21dc0`) in its
deployed image; it has not been redeployed since. A redeploy is separate infra work and is not
performed or claimed here.

## P5 — Durability on the deployed engine (2026-08-23)

`10_status_and_remaining_work.md`'s predecessor's P5 wording ("delete the running revision; cold start;
re-invoke the same invocation_id") assumes Cloud Run, which has a deletable revision resource.
The case workflow is deployed on Vertex AI Agent Runtime, a managed `reasoningEngine` with no
user-facing revision or restart API — that literal action does not exist for this compute type.

`infra/geap/verify_kill_resume.py` proves the same underlying property the way this
architecture actually allows:

```bash
uv run python infra/geap/verify_kill_resume.py
```

1. Opens a real case on the deployed Runtime; it runs the full graph and parks on the sterility
   hold (durable Cloud SQL state, not a held connection).
2. Independently confirms the persisted incubation row and its resume handle over a read-only
   Cloud SQL connection this script owns — proof the record exists outside the process that
   wrote it.
3. Resumes the invocation through `POST /scheduler/sterility-tick` on the deployed tick Cloud
   Run service — a separate process, machine, and memory space from both this script and the
   original case Runtime request — using a real, short-lived OIDC token minted for the actual
   Cloud Scheduler invoker identity (temporary `iam.serviceAccountTokenCreator` grant, revoked
   immediately after this proof ran).
4. Confirms the same `invocation_id` the case parked on resumed, and that the sterility-hold
   node in the session's own event history shows completed, not still interrupted.

Live run confirmed all of this, with one real finding along the way: the verifier's own tick
call and the live Cloud Scheduler job (`*/15 * * * *`) can race once the hold's `due_at` passes
— production infrastructure resuming the case before the script's explicit call does. This is
correct scheduler behavior, not a bug, so the verifier accepts either outcome and always
independently confirms the persisted row's `invocation_id` afterward rather than only trusting
whichever call happened to win.

A temporary `COUNTERSIGN_STERILITY_HOLD_SECONDS=20` was set on `countersign-department-mcp` for
this run only (same reversible pattern as the 2026-08-18 kill/resume proof) and removed
immediately after; the deployed service is back to the uncompressed default.

## CFI Phase 1 — live provisioning and proof (2026-08-24)

CFI Phase 1's code (see `checklist.md`'s 2026-08-24 CFI entries) is now provisioned and proven
live, closing every item that entry left open:

- **Data plane.** `infra/geap/provision_cfi_sql.sh` created `adk_sessions.cfi_investigation` on
  the existing `countersign-sessions` Cloud SQL instance, granting the department MCP identity
  `SELECT`/`INSERT`/`UPDATE` only — no `DELETE`, no DDL. `infra/geap/provision_department_systems.sh`
  confirmed the real LIMS table and created/seeded `countersign_lims.batch_material_lots` with
  the shared-lot siblings `AX-2291`/`AX-2292`/`AX-2293`.
- **Department MCP redeploy.** `countersign-department-mcp` was redeployed with
  `COUNTERSIGN_SIBLING_BATCH_BACKEND=cloud_sql` and `COUNTERSIGN_SIBLING_BATCH_TABLE=batch_material_lots`
  set explicitly, alongside its unchanged real `COUNTERSIGN_LIMS_BACKEND=cloud_sql` and
  `COUNTERSIGN_EM_BACKEND=bigquery`. Verified live: Cloud Run env vars, private IAM (still
  `--no-allow-unauthenticated`), and an authenticated `tools/list` call all confirmed the switch
  and the four CFI tool names.
- **CFI Runtime.** `infra/geap/deploy_cfi_runtime.py` deployed a separate Agent Identity Runtime
  from `app.cfi_app` (`reasoningEngines/7328770015929303040`), never the case Runtime's image.
  Its own `countersign-cfi-invoker` service account holds `roles/run.invoker` on the department
  MCP service; the CFI Agent Identity holds `roles/iam.serviceAccountTokenCreator` on that
  invoker — the same credential-boundary pattern as the case Runtime's own invoker, but
  disjoint from it.
- **Gateway policy and Registry.** `countersign-mcp-cfi-workflow-tools` was imported as an
  `ALLOW` policy on the existing `countersign-egress` gateway, permitting only
  `sibling_batch_query`, `cfi_open`, `cfi_get`, `cfi_save` (plus `MATCH_BASE_PROTOCOL_METHODS`).
  The CFI Runtime auto-registered in Agent Registry as `countersign-cfi`.
- **Live bounded probe (`infra/geap/verify_cfi_live.py`).** Opened one explicitly named
  live-probe CFI (`44dbfd6c-79e0-4732-952c-9bc7b8772177`, tenant `aurex`, batch `AX-2291`),
  drove it through the deployed CFI Runtime's `streamQuery` (29 events, zero errors), confirmed
  same-tenant sibling expansion returned real Cloud SQL rows and a cross-tenant sibling query
  was rejected, then independently re-read the record over MCP after the run: `revision=2`,
  `outcome=root_cause_identified`, one hypothesis, and evidence citing `sibling_batch_query`.
  The probe record is retained, not deleted — this MCP surface has no delete operation, and
  audit retention is the safer default for a GMP investigation trail.
- **Two verifier-only issues found and fixed during this pass, not infra bugs:** the verifier
  originally compared the Runtime's raw resource path against Agent Registry's `agentId`, which
  is a `urn:agent:...reasoningEngines:{id}` string, not that path — fixed to match on the
  runtime id substring. It also compared the Gateway policy's target against the project-ID
  form of the gateway resource, while an imported policy canonicalizes the target to the
  project-number form — fixed to accept either.
- No tests were added or run for this pass; no commit was made. This is a live-infrastructure
  provisioning and proof pass only.

**CFI Phase 1 is now live and proven end to end.** Phase 2 (Chat interviews) and Phase 3 (trend
correlation, Calendar site review, filed CFI report) remain **not started**.
