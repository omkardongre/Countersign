/**
 * Projects raw ADK `Event` objects from the deployed case Agent Runtime into
 * the same `CaseDeskRecord` shape the fixture/in-memory desk already
 * renders (`lib/types.ts`). This is what makes the live path show up in the
 * exact same UI as the fixtures — no separate "live view".
 *
 * Routing is by node name, derived from `event.nodeInfo.path` (see
 * `getNodeName` below) — verified live that `event.author` on the deployed
 * engine is always the root graph's own name, not the node's. Node names
 * below are the literal `@node(name=...)` strings from
 * `app/domain/case_graph.py` and its collaborators — kept in one place here
 * so a rename on the Python side has one obvious place to fix on this side.
 */

import type {
  BranchOutcome,
  CaseDeskRecord,
  CaseIntake,
  ChallengerReport,
  ClassifiedCase,
  ComplianceFinding,
  Department,
  InvestigationDraft,
  QpConfirmationRecord,
  ReleaseGateResult,
  SterilityHold,
} from "@/lib/types";
import type { EngineEvent, EngineFunctionCall } from "./engine-client";

const REQUEST_INPUT_FUNCTION_CALL_NAME = "adk_request_input";

const NODE = {
  intake: "intake_and_bind_evidence",
  collectManufacturing: "collect_manufacturing",
  collectQc: "collect_qc",
  collectSupply: "collect_supply",
  reconcile: "reconcile_departments",
  priorArt: "retrieve_prior_art",
  draft: "draft_investigation",
  challenge: "challenge_investigation",
  investigationGate: "gate_investigation",
  sterility: "await_sterility_incubation",
  releaseGate: "evaluate_annex16_release_gate",
  qp: "confirm_qp",
  caseMemory: "record_case_memory",
} as const;

export type PendingInterrupt = {
  interruptId: string;
  functionName: string;
  node: string;
  message: string;
  payload: Record<string, unknown>;
};

export type LiveProjection = {
  record: CaseDeskRecord;
  pendingInterrupt: PendingInterrupt | null;
};

function getFunctionCall(event: EngineEvent): EngineFunctionCall | null {
  const content = event.content as { parts?: unknown[] } | undefined;
  const part = content?.parts?.[0] as
    | { function_call?: EngineFunctionCall; functionCall?: EngineFunctionCall }
    | undefined;
  return part?.function_call ?? part?.functionCall ?? null;
}

/**
 * The node that actually produced this event.
 *
 * `event.author` is NOT the node name on the deployed engine — it is always
 * the root graph's own name (`"deviation_case"`), verified live against a
 * parked AX-3001 session (every event, including the `await_sterility_incubation`
 * pause, carries `author: "deviation_case"`). The real per-step node name is
 * the last `name@n` segment of `event.nodeInfo.path`
 * (`"deviation_case@1/await_sterility_incubation@1"` -> `"await_sterility_incubation"`).
 * Falls back to `author` for events with no path (e.g. the initial `user`
 * event), which never match any `NODE.*` case anyway.
 */
function getNodeName(event: EngineEvent): string | undefined {
  const nodeInfo = event.nodeInfo as { path?: string } | undefined;
  const path = nodeInfo?.path;
  if (path) {
    const lastSegment = path.split("/").pop() ?? "";
    const name = lastSegment.split("@")[0];
    if (name) return name;
  }
  return event.author as string | undefined;
}

function isRequestInput(event: EngineEvent): boolean {
  const fc = getFunctionCall(event);
  return fc?.name === REQUEST_INPUT_FUNCTION_CALL_NAME;
}

function branchesFromCase(caseObj: Record<string, unknown>): {
  branches: BranchOutcome[];
  findings: ComplianceFinding[];
} {
  const branches = (caseObj.branches ?? []) as BranchOutcome[];
  const findings = (caseObj.findings ?? []) as ComplianceFinding[];
  return { branches, findings };
}

/**
 * Builds a `CaseDeskRecord` (and, if the graph is currently paused, the
 * pending `RequestInput`) from the accumulated events of one live session.
 *
 * `intake` and `classified` seed values come from the caller (the case as
 * it was opened) because `intake_and_bind_evidence`'s output is the
 * fan-out bundle, not the desk's richer `CaseIntake`/`ClassifiedCase`
 * shapes — those are known before the graph ever runs.
 */
export function projectLiveEvents(
  events: EngineEvent[],
  seed: { intake: CaseIntake; classified: ClassifiedCase },
): LiveProjection {
  const record: CaseDeskRecord = {
    data_class: "labelled_synthetic",
    origin: "intake",
    desk_stage: "classified",
    intake: seed.intake,
    classified: seed.classified,
    aggregated_status: null,
    branches: [],
    findings: [],
    prior_art: [],
    prior_art_embedded: false,
    extracted_fields: [],
    draft: null,
    challenger: null,
    investigation_gate: null,
    release_gate: null,
    sterility_hold: null,
    qp_confirmation: null,
    decision_authority: "none",
  };

  let pendingInterrupt: PendingInterrupt | null = null;
  const branchByDept = new Map<Department, BranchOutcome>();

  for (const event of events) {
    const author = getNodeName(event);
    const output = event.output as Record<string, unknown> | null | undefined;

    if (isRequestInput(event) && author) {
      const fc = getFunctionCall(event)!;
      pendingInterrupt = {
        interruptId: fc.id,
        functionName: fc.name,
        node: author,
        message: String(fc.args.message ?? ""),
        payload: (fc.args.payload as Record<string, unknown>) ?? {},
      };
      if (author === NODE.sterility) {
        const brief = pendingInterrupt.payload;
        record.sterility_hold = {
          incubation_days: Math.round(
            (Number(brief.hold_seconds) || 0) / 86400,
          ),
          started_at: String(brief.started_at ?? ""),
          due_at: String(brief.due_at ?? ""),
          status: "incubating",
        } satisfies SterilityHold;
        record.desk_stage = "sterility_hold";
      } else if (author === NODE.qp) {
        record.desk_stage = "pending_qp";
      }
      continue; // A pause is the last meaningful event for this run.
    }

    if (!output || !author) continue;

    switch (author) {
      case NODE.collectManufacturing:
      case NODE.collectQc:
      case NODE.collectSupply: {
        const branch = output as unknown as BranchOutcome;
        branchByDept.set(branch.department, branch);
        record.branches = Array.from(branchByDept.values());
        break;
      }
      case NODE.reconcile: {
        const { branches, findings } = branchesFromCase(output);
        record.branches = branches;
        record.findings = findings;
        record.aggregated_status =
          (output.aggregated_status as CaseDeskRecord["aggregated_status"]) ??
          null;
        break;
      }
      case NODE.priorArt: {
        record.prior_art =
          (output.prior_art as CaseDeskRecord["prior_art"]) ?? [];
        record.prior_art_embedded = Boolean(output.prior_art_embedded);
        break;
      }
      case NODE.draft: {
        if (!output.skipped) {
          record.draft = (output.draft as InvestigationDraft) ?? null;
        }
        break;
      }
      case NODE.challenge: {
        if (!output.skipped) {
          record.challenger = (output.challenger as ChallengerReport) ?? null;
        }
        break;
      }
      case NODE.investigationGate: {
        record.investigation_gate =
          (output.gate as CaseDeskRecord["investigation_gate"]) ?? null;
        break;
      }
      case NODE.sterility: {
        // Resumed past the hold without pausing this run (already complete).
        if (record.sterility_hold) {
          record.sterility_hold = {
            ...record.sterility_hold,
            status: "ticked",
          };
        }
        break;
      }
      case NODE.releaseGate: {
        // The QP RequestInput pause fires immediately after this node, so
        // desk_stage moves to pending_qp there (see the interrupt branch
        // above), not here.
        record.release_gate = (output.release_gate as ReleaseGateResult) ?? null;
        break;
      }
      case NODE.qp: {
        const qp = (output.qp_confirmation as QpConfirmationRecord) ?? null;
        record.qp_confirmation = qp;
        record.desk_stage = qp && qp.outcome !== "rejected" ? "qp_recorded" : "pending_qp";
        if (qp) {
          record.classified = {
            ...record.classified,
            lifecycle_state:
              qp.outcome === "rejected" ? "pending_qp" : "dispositioned",
          };
        }
        break;
      }
      case NODE.caseMemory: {
        // Pass-through node; qp_confirmation was already set by NODE.qp.
        break;
      }
      default:
        break;
    }
  }

  return { record, pendingInterrupt };
}
