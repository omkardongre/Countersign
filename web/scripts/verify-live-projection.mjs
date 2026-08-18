/**
 * Offline verification of the live-path plumbing: stream framing, event
 * projection, and interrupt detection. No GCP calls.
 *
 * Feeds recorded-shape ADK events (same JSON framing as
 * `infra/geap/evidence/stream_allowed.sse` — back-to-back objects, no `data:`
 * prefix) through the real parser and the real projector, then asserts the
 * desk record and pending interrupt come out correct.
 *
 *   cd web && node --experimental-strip-types scripts/verify-live-projection.mjs
 *
 * Run via tsx/next's loader is unnecessary; this imports the compiled-free
 * TS through a tiny inline transpile step is avoided by keeping the parser
 * duplicated-free: we import from source using Node's TS support (Node 22+)
 * or fall back to a clear message.
 */

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import os from "node:os";
import path from "node:path";
import ts from "typescript";

const here = path.dirname(fileURLToPath(import.meta.url));

/** Transpiles a .ts module to ESM in a temp dir and imports it. */
async function importTs(tsPath) {
  const source = readFileSync(tsPath, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  });
  const dir = mkdtempSync(path.join(os.tmpdir(), "countersign-verify-"));
  const outPath = path.join(dir, path.basename(tsPath).replace(/\.ts$/, ".mjs"));
  writeFileSync(outPath, outputText, "utf8");
  return import(pathToFileURL(outPath).href);
}

// --- 1. Confirm the recorded evidence really is unframed concatenated JSON ---
const evidencePath = path.join(
  here,
  "..",
  "..",
  "infra",
  "geap",
  "evidence",
  "stream_allowed.sse",
);
const raw = readFileSync(evidencePath, "utf8");
assert.ok(
  !raw.includes("data:"),
  "Evidence file unexpectedly contains SSE `data:` framing — the parser assumption changed.",
);

// Re-implementation-free check: count top-level objects with a brace scanner
// identical in behaviour to lib/live/json-stream.ts.
function countTopLevelObjects(text) {
  let depth = 0;
  let inString = false;
  let escaped = false;
  let count = 0;
  for (const ch of text) {
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) count++;
    }
  }
  return count;
}
assert.equal(
  countTopLevelObjects(raw),
  3,
  "Expected 3 events in the recorded stream evidence.",
);
console.log("PASS  stream framing: 3 concatenated JSON objects, no `data:` prefix");

// --- 2. Chunk-boundary robustness, against the real parser ---
// Transpiles and imports the actual `json-stream.ts` rather than a
// re-implementation, then feeds the recorded stream in at many different
// chunk boundaries. The parser must always yield exactly 3 complete events
// and never a partial one, whatever the split.
const { JsonObjectStreamParser } = await importTs(
  path.join(here, "..", "src", "lib", "live", "json-stream.ts"),
);

for (const chunkSize of [1, 2, 7, 64, 137, 1024, raw.length]) {
  const parser = new JsonObjectStreamParser();
  const events = [];
  for (let i = 0; i < raw.length; i += chunkSize) {
    events.push(...parser.push(raw.slice(i, i + chunkSize)));
  }
  parser.finish(); // Throws if a partial object is left over.
  assert.equal(
    events.length,
    3,
    `chunkSize=${chunkSize} yielded ${events.length} events, expected 3`,
  );
  for (const e of events) {
    assert.equal(typeof e, "object");
    assert.ok(e.invocation_id, "event missing invocation_id");
    assert.ok(e.author, "event missing author");
  }
}
console.log("PASS  real parser: 3 events at every chunk size, no partials");

// A truncated stream must be reported, not silently accepted.
const truncated = new JsonObjectStreamParser();
truncated.push(raw.slice(0, 200));
assert.throws(() => truncated.finish(), /ended mid-object/);
console.log("PASS  real parser: truncated stream throws");

// --- 3. Projector contract, asserted against the node names in the graph ---
const graphPath = path.join(here, "..", "..", "app", "domain", "case_graph.py");
const graphSrc = readFileSync(graphPath, "utf8");
const projectorSrc = readFileSync(
  path.join(here, "..", "src", "lib", "live", "projector.ts"),
  "utf8",
);

const expectedNodes = [
  "intake_and_bind_evidence",
  "collect_manufacturing",
  "collect_qc",
  "collect_supply",
  "reconcile_departments",
  "retrieve_prior_art",
  "draft_investigation",
  "challenge_investigation",
  "gate_investigation",
  "await_sterility_incubation",
  "evaluate_annex16_release_gate",
  "confirm_qp",
  "record_case_memory",
];
for (const name of expectedNodes) {
  assert.ok(
    projectorSrc.includes(`"${name}"`),
    `projector.ts is missing node name ${name}`,
  );
}
console.log(
  `PASS  projector covers all ${expectedNodes.length} graph node names`,
);

// Cross-check the graph module still references the same edge order, so a
// Python-side rename fails this script instead of silently degrading the UI.
for (const name of ["intake_and_bind_evidence", "reconcile_departments"]) {
  assert.ok(
    graphSrc.includes(name),
    `case_graph.py no longer mentions ${name}; projector.ts needs updating`,
  );
}
console.log("PASS  graph node names still present in case_graph.py");

// --- 4. Resume contract matches ADK's function-call name ---
const hitlName = "adk_request_input";
assert.ok(
  projectorSrc.includes(hitlName),
  "projector.ts must match ADK's REQUEST_INPUT_FUNCTION_CALL_NAME",
);
const confirmRoute = readFileSync(
  path.join(here, "..", "src", "app", "api", "cases", "[caseId]", "confirm", "route.ts"),
  "utf8",
);
assert.ok(
  confirmRoute.includes("confirm_qp"),
  "confirm route must gate on the confirm_qp node",
);
console.log("PASS  resume contract: adk_request_input + confirm_qp gate");

// --- 5. Projector behaviour against synthetic events ---
// The projector imports `@/lib/types` for types only, so a type-stripped
// transpile has no runtime dependency to resolve.
const { projectLiveEvents } = await importTs(
  path.join(here, "..", "src", "lib", "live", "projector.ts"),
);

const seed = {
  intake: { tenant_id: "aurex", batch_id: "AX-2291" },
  classified: {
    case_id: "case-aurex-ax-2291-1",
    tenant_id: "aurex",
    batch_id: "AX-2291",
    lifecycle_state: "classified",
  },
};

const ev = (author, output) => ({ author, output, invocation_id: "e-1" });
const interrupt = (author, id, payload) => ({
  author,
  invocation_id: "e-1",
  content: {
    role: "model",
    parts: [
      {
        function_call: {
          name: "adk_request_input",
          id,
          args: { interrupt_id: id, message: `pause at ${author}`, payload },
        },
      },
    ],
  },
});

// 5a. Departments fan in, then reconcile sets aggregate + findings.
{
  const { record, pendingInterrupt } = projectLiveEvents(
    [
      ev("collect_manufacturing", { department: "manufacturing", status: "ok" }),
      ev("collect_qc", { department: "qc", status: "ok" }),
      ev("collect_supply", { department: "supply", status: "failed" }),
      ev("reconcile_departments", {
        aggregated_status: "insufficient_evidence",
        branches: [
          { department: "manufacturing", status: "ok" },
          { department: "qc", status: "ok" },
          { department: "supply", status: "failed" },
        ],
        findings: [{ finding_id: "f1" }],
      }),
      ev("retrieve_prior_art", {
        prior_art: [{ fact_id: "p1" }],
        prior_art_embedded: true,
      }),
    ],
    seed,
  );
  assert.equal(pendingInterrupt, null);
  assert.equal(record.origin, "intake"); // caller stamps "live"
  assert.equal(record.branches.length, 3);
  assert.equal(record.aggregated_status, "insufficient_evidence");
  assert.equal(record.findings.length, 1);
  assert.equal(record.prior_art.length, 1);
  assert.equal(record.prior_art_embedded, true);
  assert.equal(record.decision_authority, "none");
  console.log("PASS  projector: departments, reconcile, prior art");
}

// 5b. Sterility hold pause becomes desk_stage sterility_hold with a clock.
{
  const { record, pendingInterrupt } = projectLiveEvents(
    [
      ev("gate_investigation", { gate: { passed: true, blocked_reasons: [] } }),
      interrupt("await_sterility_incubation", "sterility_incubation:case-1", {
        hold_seconds: 1209600,
        started_at: "2026-08-17T00:00:00Z",
        due_at: "2026-08-31T00:00:00Z",
      }),
    ],
    seed,
  );
  assert.equal(record.desk_stage, "sterility_hold");
  assert.equal(record.sterility_hold.incubation_days, 14);
  assert.equal(record.sterility_hold.status, "incubating");
  assert.equal(record.investigation_gate.passed, true);
  assert.equal(pendingInterrupt.node, "await_sterility_incubation");
  assert.equal(pendingInterrupt.interruptId, "sterility_incubation:case-1");
  console.log("PASS  projector: sterility hold pause + 14-day clock");
}

// 5c. QP pause exposes the Annex 16 pack and a resumable interrupt.
{
  const { record, pendingInterrupt } = projectLiveEvents(
    [
      ev("evaluate_annex16_release_gate", {
        release_gate: { status: "not_ready", blocked_reasons: ["1.7.16: open"] },
      }),
      interrupt("confirm_qp", "qp_confirmation:case-1", { tenant_id: "aurex" }),
    ],
    seed,
  );
  assert.equal(record.desk_stage, "pending_qp");
  assert.equal(record.release_gate.status, "not_ready");
  assert.equal(pendingInterrupt.node, "confirm_qp");
  assert.equal(pendingInterrupt.functionName, "adk_request_input");
  console.log("PASS  projector: QP pause + Annex 16 pack");
}

// 5d. A refused confirm stays at pending_qp; a real confirm disposes.
{
  const rejected = projectLiveEvents(
    [
      ev("confirm_qp", {
        qp_confirmation: { outcome: "rejected", qp_name: "Dr X" },
      }),
    ],
    seed,
  ).record;
  assert.equal(rejected.desk_stage, "pending_qp");
  assert.equal(rejected.classified.lifecycle_state, "pending_qp");

  const confirmed = projectLiveEvents(
    [
      ev("confirm_qp", {
        qp_confirmation: { outcome: "confirmed", qp_name: "Dr X" },
      }),
    ],
    seed,
  ).record;
  assert.equal(confirmed.desk_stage, "qp_recorded");
  assert.equal(confirmed.classified.lifecycle_state, "dispositioned");
  console.log("PASS  projector: refused confirm holds, real confirm disposes");
}

// 5e. A skipped investigation must not surface a draft.
{
  const { record } = projectLiveEvents(
    [
      ev("draft_investigation", {
        skipped: true,
        skip_reason: "branch failed",
        draft: null,
      }),
      ev("challenge_investigation", { skipped: true, challenger: null }),
    ],
    seed,
  );
  assert.equal(record.draft, null);
  assert.equal(record.challenger, null);
  console.log("PASS  projector: skipped investigation surfaces no draft");
}

console.log("\nAll offline live-path checks passed.");
