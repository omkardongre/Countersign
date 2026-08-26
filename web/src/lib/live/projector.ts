/**
 * Projects raw ADK `Event` objects from the deployed case and CFI Agent
 * Runtimes into the desk's shared `CaseDeskRecord` shape. The CFI runtime is
 * separate from the case runtime; callers may pass its event stream through
 * `cfiEvents` when it is available. Fixture and live records therefore use
 * the same panel without fabricating a second CFI data model.
 *
 * Routing is by node name, derived from `event.nodeInfo.path` (see
 * `getNodeName` below) — `event.author` on the deployed engine is the root
 * graph's own name, not the node's. Node names below are the literal
 * `@node(name=...)` strings from the Python graphs.
 */

import type {
  BranchOutcome,
  CaseDeskRecord,
  CaseIntake,
  CfiActivity,
  CfiDeskState,
  CfiEvidenceItem,
  CfiInterviewStatus,
  CfiInterviewSummary,
  CfiRecord,
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
  collectEnvironmentalMonitoring: "collect_environmental_monitoring",
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
  cfiHandoff: "escalate_to_cfi",
  cfiLoad: "load_cfi",
  cfiPlan: "cfi_plan_round",
  cfiDispatch: "dispatch_enquiry",
  cfiScope: "run_scope_expansion",
  cfiInterview: "run_interview",
  cfiTrend: "run_trend_correlation",
  cfiEvidence: "record_evidence",
  cfiContinue: "cfi_continue_gate_node",
  cfiClosure: "cfi_closure_node",
  cfiSiteReview: "schedule_site_review",
  cfiReport: "file_cfi_report",
} as const;

const CFI_NODES = new Set<string>([
  NODE.cfiLoad,
  NODE.cfiPlan,
  NODE.cfiDispatch,
  NODE.cfiScope,
  NODE.cfiInterview,
  NODE.cfiTrend,
  NODE.cfiEvidence,
  NODE.cfiContinue,
  NODE.cfiClosure,
  NODE.cfiSiteReview,
  NODE.cfiReport,
]);

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
 * `event.author` is not the node name on the deployed engine — it is always
 * the root graph's own name. The real per-step node name is the last
 * `name@n` segment of `event.nodeInfo.path`.
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

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function eventTime(event: EngineEvent): string | null {
  const timestamp = event.timestamp ?? event.time;
  return typeof timestamp === "string" ? timestamp : null;
}

function isCfiRecord(value: Record<string, unknown>): boolean {
  return (
    typeof value.cfi_id === "string" &&
    typeof value.tenant_id === "string" &&
    typeof value.source_case_id === "string" &&
    typeof value.batch_id === "string" &&
    typeof value.opened_at === "string" &&
    Array.isArray(value.trigger_reasons) &&
    Array.isArray(value.hypotheses) &&
    Array.isArray(value.evidence) &&
    asRecord(value.budget) !== null
  );
}

/** CFI round nodes wrap the durable aggregate in `investigation`. */
function cfiRecordFromOutput(output: unknown): Record<string, unknown> | null {
  const object = asRecord(output);
  if (!object) return null;
  if (isCfiRecord(object)) return object;
  const investigation = asRecord(object.investigation);
  return investigation && isCfiRecord(investigation) ? investigation : null;
}

function cfiStatus(value: unknown, fallback: CfiInterviewStatus): CfiInterviewStatus {
  const statuses: CfiInterviewStatus[] = [
    "created",
    "question_send_pending",
    "waiting_for_reply",
    "polling",
    "reply_captured",
    "completed",
    "interview_unanswered",
    "failed",
  ];
  return typeof value === "string" && statuses.includes(value as CfiInterviewStatus)
    ? (value as CfiInterviewStatus)
    : fallback;
}

function interviewFromPayload(
  cfi: CfiDeskState,
  payload: Record<string, unknown>,
  fallbackStatus: CfiInterviewStatus,
): CfiInterviewSummary | null {
  const interviewId = stringValue(payload.interview_id);
  if (!interviewId) return null;
  const existing = cfi.interviews.find((item) => item.interview_id === interviewId);
  return {
    interview_id: interviewId,
    cfi_id: stringValue(payload.cfi_id) ?? cfi.cfi_id,
    hypothesis_id: stringValue(payload.hypothesis_id) ?? existing?.hypothesis_id ?? null,
    round_number:
      typeof payload.round_number === "number"
        ? payload.round_number
        : existing?.round_number ?? null,
    status: cfiStatus(payload.status, fallbackStatus),
    question:
      stringValue(payload.question) ?? existing?.question ?? null,
    thread_name:
      stringValue(payload.thread_name) ?? existing?.thread_name ?? null,
    deadline_at:
      stringValue(payload.deadline_at) ?? existing?.deadline_at ?? null,
    nudge_count:
      typeof payload.nudge_count === "number"
        ? payload.nudge_count
        : existing?.nudge_count ?? 0,
    max_nudges:
      typeof payload.max_nudges === "number"
        ? payload.max_nudges
        : existing?.max_nudges ?? null,
    replies: existing?.replies ?? [],
    outcome_summary:
      stringValue(payload.outcome_summary) ?? existing?.outcome_summary ?? null,
    decision_authority: "none",
  };
}

function interviewFromRoundOutput(
  cfi: CfiDeskState,
  output: Record<string, unknown>,
): CfiInterviewSummary | null {
  const pending = asRecord(output.pending_interview_result);
  if (!pending) return null;
  const interviewId = stringValue(pending.interview_id);
  if (!interviewId) return null;
  const existing = cfi.interviews.find((item) => item.interview_id === interviewId);
  return {
    interview_id: interviewId,
    cfi_id: cfi.cfi_id,
    hypothesis_id: existing?.hypothesis_id ?? null,
    round_number: existing?.round_number ?? null,
    status: cfiStatus(pending.status, "completed"),
    question: existing?.question ?? null,
    thread_name: existing?.thread_name ?? null,
    deadline_at: existing?.deadline_at ?? null,
    nudge_count: existing?.nudge_count ?? 0,
    max_nudges: existing?.max_nudges ?? null,
    replies: existing?.replies ?? [],
    outcome_summary: stringValue(pending.summary),
    decision_authority: "none",
  };
}

function withInterview(
  cfi: CfiDeskState,
  interview: CfiInterviewSummary | null,
): CfiDeskState {
  if (!interview) return cfi;
  const interviews = cfi.interviews.filter(
    (item) => item.interview_id !== interview.interview_id,
  );
  return { ...cfi, interviews: [...interviews, interview] };
}

function cfiKind(node: string): CfiActivity["kind"] {
  switch (node) {
    case NODE.cfiLoad:
    case NODE.cfiHandoff:
      return "handoff";
    case NODE.cfiPlan:
      return "planning";
    case NODE.cfiDispatch:
    case NODE.cfiScope:
    case NODE.cfiTrend:
      return "dispatch";
    case NODE.cfiInterview:
      return "interview";
    case NODE.cfiEvidence:
    case NODE.cfiContinue:
      return "evidence";
    case NODE.cfiClosure:
      return "closure";
    case NODE.cfiSiteReview:
    case NODE.cfiReport:
      return "artifact";
    default:
      return "evidence";
  }
}

function cfiActivityDetail(node: string, output: unknown): string {
  const object = asRecord(output);
  const investigation = asRecord(object?.investigation) ?? object;
  if (node === NODE.cfiPlan) {
    return stringValue(object?.rationale) ?? "CFI planning round completed.";
  }
  if (node === NODE.cfiDispatch) {
    return stringValue(object?.enquiry_kind)
      ? `Enquiry routed to ${String(object?.enquiry_kind)}.`
      : "CFI enquiry route evaluated.";
  }
  if (node === NODE.cfiScope || node === NODE.cfiTrend) {
    return "Tenant-locked historical enquiry completed; captured tool output is the evidence source.";
  }
  if (node === NODE.cfiInterview) {
    const pending = asRecord(object?.pending_interview_result);
    return stringValue(pending?.summary) ?? "Interview checkpoint advanced.";
  }
  if (node === NODE.cfiEvidence) {
    const evidence = Array.isArray(investigation?.evidence)
      ? (investigation.evidence as unknown[])
      : [];
    const latest = asRecord(evidence[evidence.length - 1]);
    return stringValue(latest?.fact) ?? "Captured CFI evidence.";
  }
  if (node === NODE.cfiClosure) {
    const outcome = stringValue(investigation?.outcome);
    return outcome
      ? `CFI closed with outcome ${outcome}.`
      : "CFI closure evaluated.";
  }
  if (node === NODE.cfiSiteReview) {
    if (investigation?.site_review_error) {
      return `Site-review filing error: ${String(investigation.site_review_error)}`;
    }
    return investigation?.site_review
      ? "Calendar site-review reference recorded."
      : "Calendar site-review attempt completed without a reference.";
  }
  if (node === NODE.cfiReport) {
    if (investigation?.report_error) {
      return `Report filing error: ${String(investigation.report_error)}`;
    }
    return investigation?.report
      ? "Deterministic CFI report reference recorded."
      : "Report filing attempt completed without a document reference.";
  }
  return "CFI state updated.";
}

function appendCfiActivity(
  cfi: CfiDeskState,
  event: EngineEvent,
  node: string,
  output: unknown,
  index: number,
): CfiDeskState {
  const invocation = stringValue(event.invocation_id) ?? "event";
  const activity: CfiActivity = {
    activity_id: `${invocation}:${index}:${node}`,
    node,
    kind: cfiKind(node),
    at: eventTime(event),
    detail: cfiActivityDetail(node, output),
    decision_authority: "none",
  };
  if (cfi.activity.some((item) => item.activity_id === activity.activity_id)) {
    return cfi;
  }
  return { ...cfi, activity: [...cfi.activity, activity] };
}

function mergeCfiRecord(
  current: CfiDeskState | null,
  candidate: Record<string, unknown>,
): CfiDeskState {
  const aggregate = candidate as unknown as CfiRecord;
  const base: CfiDeskState = current
    ? { ...current, ...aggregate }
    : {
        ...aggregate,
        interviews: [],
        activity: [],
      };
  return {
    ...base,
    interviews: current?.interviews ?? [],
    activity: current?.activity ?? [],
    decision_authority: "none",
  };
}

/**
 * Builds a `CaseDeskRecord` (and, if a graph is currently paused, the
 * pending `RequestInput`) from accumulated live events. `cfiEvents` is
 * optional because the case and CFI graphs run as separate Agent Runtimes;
 * when the CFI stream is available, it is projected into the same record.
 */
export function projectLiveEvents(
  events: EngineEvent[],
  seed: { intake: CaseIntake; classified: ClassifiedCase },
  options: { cfiEvents?: EngineEvent[] } = {},
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
    cfi: null,
    decision_authority: "none",
  };

  let pendingInterrupt: PendingInterrupt | null = null;
  const branchByDept = new Map<Department, BranchOutcome>();
  const allEvents = [...events, ...(options.cfiEvents ?? [])];

  for (const [index, event] of allEvents.entries()) {
    const author = getNodeName(event);
    const output = event.output as Record<string, unknown> | null | undefined;
    const isCfiNode = Boolean(author && CFI_NODES.has(author));

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
      } else if (author === NODE.cfiInterview && record.cfi) {
        const interview = interviewFromPayload(
          record.cfi,
          pendingInterrupt.payload,
          "waiting_for_reply",
        );
        record.cfi = withInterview(record.cfi, interview);
        record.cfi = appendCfiActivity(
          record.cfi,
          event,
          author,
          { pending_interview_result: null },
          index,
        );
      }
      continue; // A pause is the last meaningful event for this run.
    }

    if (isCfiNode && output) {
      const candidate = cfiRecordFromOutput(output);
      if (candidate) {
        record.cfi = mergeCfiRecord(record.cfi, candidate);
        const interview = interviewFromRoundOutput(record.cfi, output);
        record.cfi = withInterview(record.cfi, interview);
        record.cfi = appendCfiActivity(record.cfi, event, author!, output, index);
      }
      continue;
    }

    if (!output || !author) continue;

    switch (author) {
      case NODE.collectManufacturing:
      case NODE.collectQc:
      case NODE.collectEnvironmentalMonitoring:
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
        if (record.sterility_hold) {
          record.sterility_hold = {
            ...record.sterility_hold,
            status: "ticked",
          };
        }
        break;
      }
      case NODE.releaseGate: {
        record.release_gate = (output.release_gate as ReleaseGateResult) ?? null;
        break;
      }
      case NODE.qp: {
        const qp = (output.qp_confirmation as QpConfirmationRecord) ?? null;
        record.qp_confirmation = qp;
        record.desk_stage =
          qp && qp.outcome !== "rejected" ? "qp_recorded" : "pending_qp";
        if (qp) {
          record.classified = {
            ...record.classified,
            lifecycle_state:
              qp.outcome === "rejected" ? "pending_qp" : "dispositioned",
          };
        }
        break;
      }
      case NODE.caseMemory:
      case NODE.cfiHandoff:
      default:
        break;
    }
  }

  return { record, pendingInterrupt };
}

export function isCfiNodeName(node: string): boolean {
  return CFI_NODES.has(node);
}

export function cfiEvidenceFromRecord(record: CfiDeskState): CfiEvidenceItem[] {
  return record.evidence;
}
