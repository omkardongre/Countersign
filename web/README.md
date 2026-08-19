# Countersign case desk

GMP operator UI for deviation review. Not a chatbot.

```bash
cd web
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Two execution modes

The desk renders the same `CaseDeskRecord` shape either way, so a live case
appears in the same queue and the same panels as a fixture.

### Local (default, no cloud)

- Labelled synthetic fixtures (`AX-2291` not_ready, `AX-2402` ready for QP, `HX-104` sterility hold)
- Deterministic severity on intake (same rules as `app/domain/classification.py`)
- QP confirm refused while the Annex 16 pack is `not_ready`
- Intake-opened cases live in this Node process and reset on restart

### Live Agent Runtime

Runs the real deviation-case graph on the deployed Agent Runtime: department
evidence over MCP, investigation + challenger, citation gate, sterility hold,
Annex 16 checklist, QP `RequestInput` pause.

```bash
# web/.env.local
COUNTERSIGN_CASE_ENGINE_URL=projects/777448403573/locations/us-central1/reasoningEngines/4625084117367652352
```

Then authenticate the Node process with Application Default Credentials:

```bash
gcloud auth application-default login
```

The resource name is in `case_deployment_metadata.json` (`case_agent_runtime_id`).

Open a case with **"Run on the deployed Agent Runtime"** checked on `/open`.

### Drive Picker (manufacturing evidence)

Instead of pasting a Drive file id, `/open` shows a **"Choose from Drive"** button
that opens the real Google Drive file picker. Requires two more vars in
`web/.env.local`:

```bash
NEXT_PUBLIC_GOOGLE_CLIENT_ID=<OAuth 2.0 Web application client id>
NEXT_PUBLIC_GOOGLE_API_KEY=<browser API key restricted to Drive API + Picker API>
```

The operator signs in with their own Google account (browser OAuth,
`drive.readonly` scope) and picks a file from their Drive. The picked file's id
is set into the same `drive_file_id` field the manual input used — no change
elsewhere in the live path. See `checklist.md` for the exact Console steps used
to create these credentials and what to update before deploying past
`localhost:3000`.

## How the live path works

| Concern | Implementation |
|---|---|
| Transport | `POST {engine}:streamQuery?alt=sse` — despite `alt=sse` the response is back-to-back JSON objects with no `data:` framing (verified against `infra/geap/evidence/stream_allowed.sse`), parsed by `lib/live/json-stream.ts` |
| Auth | Application Default Credentials via `google-auth-library`, same identity as `gcloud auth print-access-token` in `infra/geap/verify_governance.sh` |
| Start | `async_stream_query` with the `DeviationCaseRequest` as a JSON text part |
| Progress | `lib/live/projector.ts` maps `event.author` (the `@node(name=...)` string) onto desk fields |
| Waiting | **Polling, never a held stream.** A single streaming response is capped at 15 minutes and the sterility hold runs for days, so `GET /api/cases/[caseId]` re-reads the Runtime session and re-projects it. `components/live-poller.tsx` polls every 4s and stops after 15 minutes |
| Resume | `POST /api/cases/[caseId]/confirm` replies to the pending `adk_request_input` function call; `invocation_id` is resolved by the Runner from the matching call in session history |

## Known limits in this slice

- **A fresh live case parks at the sterility hold**, which comes before the
  Annex 16 gate. The incubation store is authoritative — the desk cannot
  advance it. The authenticated Cloud Scheduler tick service completes the
  hold and resumes the parked invocation; the desk then picks up the new
  events on its next poll. Deploy the case Runtime with
  `--sterility-hold-seconds` to compress the wait for a demo run.
- Evidence for manufacturing is a **real Google Drive file**, not a
  placeholder. Opening a live case requires a Drive file id (shown on
  `/open` once "Run on the deployed Agent Runtime" is checked); the
  manufacturing branch pulls the actual bytes over the
  `drive_fetch_evidence` MCP tool. There is no file-upload UI yet — the
  file must already exist in Drive, shared read-only with the department
  MCP service account.
- The live session pointer lives in this Node process. The Runtime session
  itself is durable (Vertex-managed); restarting `next dev` loses the desk's
  pointer to it, not the case.
- **No authentication on this desk.** It exposes case data and a QP
  confirmation action. Put identity in front of it before hosting it
  publicly.
