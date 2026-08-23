# Countersign

An enterprise agent control plane for contract pharmaceutical manufacturing, built on the
Gemini Enterprise Agent Platform. Countersign makes a CDMO's **Technical Quality Agreement**
machine-executable, and demonstrates it through GMP deviation investigation and batch
disposition.

Google's Agent Gateway answers *who* and *what*. Countersign answers *whether the Quality
Agreement permits this party to assess this thing, at this lifecycle stage, for this sponsor*.

> **Status: control plane proven, agent fleet real (8 of 8), real systems wired but not yet
> switched on live or deployed.** See [Build status](#build-status). Nothing in this README
> describes behaviour that is not currently running — where something is unbuilt it is marked
> as such.
>
> **2026-08-20 — fleet conversion (P-A).** The five department/reasoning nodes that used to be
> plain Python functions and no-tool LLM calls are now real `google.adk.agents.Agent(mode=
> "single_turn")` nodes, each bound to its own MCP-backed tool(s), placed directly in the
> `Workflow` graph. Deterministic checks still run only on each tool's captured typed
> snapshot, never on an agent's own text. See [Real agent fleet](#real-agent-fleet).

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
| Runtime | Agent Runtime, `gemini-3.6-flash` via Vertex AI |
| Durable state | Cloud SQL Postgres via ADK `DatabaseSessionService` |
| Case memory | Firestore + embeddings (provenance and tenant filtering that Memory Bank cannot round-trip); code done, not yet live |
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
| UI, demo | Case desk in `web/` — queue, case, open intake, Annex 16 + QP panel. Live Agent Runtime path wired and run live: `streamQuery` start, poll-based progress (no held SSE), `RequestInput` resume for QP confirm. Set `COUNTERSIGN_CASE_ENGINE_URL` + ADC. A projector bug that misread which node paused (fixed 2026-08-18) is why an earlier live run appeared stuck on `Classify`; architecture diagram and demo film not started |
| Manufacturing evidence over Drive | Done live (2026-08-19) — `drive_fetch_evidence` MCP tool reads a real scanned BMR page from Google Drive; `collect_manufacturing` pulls it by file id instead of trusting pushed bytes. No placeholder evidence remains on the live path |
| **P-L: real multi-day sterility hold (uncompressed)** | **Done live (2026-08-20)** — a disposable case (`aurex`/`LONGHOLD-PL-01`) opened on the deployed Runtime with no `COUNTERSIGN_STERILITY_HOLD_SECONDS` set. Confirmed from the live session event: `compressed_for_demo: false`, `hold_seconds: 1209600` (14 days exactly), `started_at: 2026-08-20T17:58:11Z`, `due_at: 2026-09-03T17:58:11Z`. Not to be ticked/resumed before `due_at` — it is the real wall-clock resume proof for the demo film. Details in `checklist.md` |
| **P-A: real agent fleet conversion** | **Done except sponsor peer (2026-08-20)** — see [Real agent fleet](#real-agent-fleet). Manufacturing, QC lab, environmental monitoring, supply, and investigation are now real `Agent(mode="single_turn")` nodes with their own bound tools; deterministic checks run only on each tool's captured snapshot. Challenger stays tool-less by design. Fleet manifest live at `GET /fleet/manifest`. Sponsor A2A peer (agent 8 of 8) not started |
| **P1: real Cloud SQL LIMS / BigQuery EM** | **Code done, not yet deployed (2026-08-21)** — `app/domain/department_source.py` lets `qc_lab_agent`/`environmental_monitoring_agent` read a real Cloud SQL table / BigQuery table instead of the synthetic catalog, behind the same `lims_query`/`em_query` tool contract. Opt-in per department (`COUNTERSIGN_LIMS_BACKEND=cloud_sql`, `COUNTERSIGN_EM_BACKEND=bigquery`); unset keeps the catalog. Read-only grants only. Provisioning scripts written (`infra/geap/provision_department_systems.sh`); not yet run against the live project, so both departments are still reading the catalog in the deployed Runtime. Supplier CoA (untrusted channel) and QMS write-back are not started |
| **P-A: sponsor liaison A2A peer (agent 8 of 8)** | **Code done, not yet deployed (2026-08-21)** — `app/sponsor_liaison/` is a real, separate service (not a graph node): a tool-bound `Agent` answering exactly one scoped batch-status question, authenticated by a per-sponsor API key, denied at the tool boundary for any tenant outside that sponsor's Quality Agreement. New Cloud SQL `case_status` table and `sponsor_batch_status`/`case_status_upsert` MCP tools with their own Gateway policy (`mcp-sponsor-liaison-status-only.yaml`). Verified in-process (agent-card serves, auth 401s, scope denies, real Gemini call reached); not yet deployed to Cloud Run |

### Real agent fleet

Per `final_reseach/10_gap_analysis_and_win_plan.md` §4.1 ("the fleet must be real agents"),
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
| `supply_assurance_agent` | `coa_fetch` (untrusted channel) | Agent decides to call it; only certificate metadata (never certificate text) reaches the model as tool output; `check_coa` runs on the captured `CoaSnapshot` |
| `deviation_investigator` | `prior_art_query` | May re-query prior deviations at most once; any fact it surfaces becomes citeable only because it was captured from the real tool call, never by the agent's assertion. Citations are checked afterwards by the deterministic `investigation_gate` |
| `investigation_challenger` | none, by design | Runs with a clean context (no shared history with the investigator) so it cannot rubber-stamp its own prior wording. Attacks are recorded for the QP; they do not themselves fail the gate |

Every agent's tool calls run against tenant/batch-locked closures — the model supplies no
scope arguments, so it cannot query another tenant's or batch's data. A branch fails closed if
its agent skips a required tool call, so a `JoinNode` waiting on all four department branches
can never stall on a silently-incomplete agent turn.

### P1 — real department systems (opt-in, code done)

QC lab and environmental monitoring can each read from a real system instead of the pinned
synthetic catalog, without changing `qc_lab_agent`/`environmental_monitoring_agent`'s tool
contract or the deterministic `check_lims`/`check_em` that runs on the result:

- `COUNTERSIGN_LIMS_BACKEND=cloud_sql` — QC lab reads a real Cloud SQL Postgres table
  (`department_source_postgres.py`), `SELECT`-only, credential held by the department MCP
  process, never by the agent or the Runtime.
- `COUNTERSIGN_EM_BACKEND=bigquery` — environmental monitoring reads a real BigQuery table
  (`department_source_bigquery.py`), `bigquery.dataViewer` only, same process boundary.
- Either unset (default) keeps that department on the synthetic catalog. This is an explicit
  opt-in, not credential-detection, so a half-provisioned deployment keeps working instead of
  silently failing every QC or EM branch.
- `GET /fleet/manifest`'s `backing_system` field is resolved live from this switch, not
  hardcoded — a fix for a real prior overclaim (the manifest used to say "BigQuery" regardless
  of whether the code was reading it).
- **Not yet run against the live project.** `infra/geap/provision_department_systems.sh` and
  `bootstrap_department_systems.py` exist but have not been executed, so the deployed Runtime
  is still reading the catalog for both departments. Supplier CoA (the untrusted channel) and
  QMS write-back are separate, unstarted P1 items.

All 8 agents in the target table are now real. Not yet built: the QMS write-back agent
(`final_reseach/12_integration_expansion_plan.md` T1-1), out of scope until P0–P6 land per that
doc's own §0. The investigation↔challenger revision loop (a back-edge, per the official
[graph routes doc](https://google.github.io/adk-docs/graphs/routes/)) is a deliberate open
decision, not an oversight — it would change the current, documented rule that challenger
attacks are recorded but never auto-fail the gate.

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
- Verified in-process, not deployed: agent-card serves publicly, missing/wrong API key returns
  401, a correct key routes through the executor to a real `Agent`/`Runner` that reaches the
  real Gemini API (fails only on this shell's expired ADC — the same wall every live Gemini
  call in this session has hit). `infra/geap/deploy_sponsor_liaison.sh` exists; not yet run
  against `countersign-505107`.

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
    case_memory_mcp.py                  async client the case Runtime uses (no direct Firestore)
    case_memory_node.py                 retrieve_prior_art / record_case_memory graph nodes
    case_memory_redact.py               redact one poisoned fact (operator CLI)
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
                                           `prior_art_write` / `prior_art_redact`
    client.py                           collector MCP client (https URL or explicit in-process)
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
  verify_case_memory.py               live KNN + tenant filter + redact probe
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
review, and structured intake. Fixtures are labelled synthetic (`AX-2291` pack
`not_ready`, `AX-2402` `ready_for_qp_review`, `HX-104` on 14-day sterility hold).
Severity on new intake uses the same rules as `app/domain/classification.py`.
QP confirm is refused while the pack is `not_ready`. The desk does not hold an
SSE stream. Live Agent Runtime `streamQuery` is not proxied yet (`501` if
`source: live`). Intake-opened cases are in-process and reset on restart.

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

- `final_reseach/ARCHITECTURE.md` — build source of truth (merged 04+05+07)
- `final_reseach/10_gap_analysis_and_win_plan.md` — remaining work
- `final_reseach/11_demo_and_proof_plan.md` — demo beats
- [Agent Gateway overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/agent-gateway-overview)
- [Set up Agent Gateway](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/set-up-agent-gateway)
- [Delegate authorization with Service Extensions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/delegate-authorization)
- [Route Agent Runtime traffic through Agent Gateway](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-gateway-runtime-deploy)
- [Troubleshoot Agent Gateway connectivity](https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/troubleshoot-agent-gateway)
- [Authenticate using an agent's own authority](https://docs.cloud.google.com/iam/docs/auth-agent-own-identity)
- [EU GMP Annex 22 draft (EC consultation)](https://health.ec.europa.eu/document/download/5f38a92d-bb8e-4264-8898-ea076e926db6_en)
- [FDA Warning Letter 320-26-58](https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/warning-letters/purolea-cosmetics-lab-722591-04022026)
