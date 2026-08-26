export const DECISION_AUTHORITY = "none" as const;

export type DeviationSeverity = "minor" | "major" | "critical";
export type LifecycleState =
  | "detected"
  | "contained"
  | "classified"
  | "under_investigation"
  | "pending_qp"
  | "dispositioned";
export type DocType =
  | "batch_record"
  | "lab_result"
  | "supplier_coa"
  | "operator_report"
  | "environmental_log";
export type ProductQualityImpact = "none" | "potential" | "confirmed";
export type ComplianceStatus =
  | "sufficient_for_investigation"
  | "insufficient_evidence";
export type FindingType =
  | "completeness"
  | "out_of_specification"
  | "out_of_trend";
export type RootCauseCategory =
  | "material"
  | "machine"
  | "method"
  | "measurement"
  | "personnel"
  | "environment"
  | "unclassified";
export type Department =
  | "manufacturing"
  | "qc"
  | "environmental_monitoring"
  | "supply";
export type BranchStatus = "ok" | "failed";
export type ChecklistItemStatus = "pass" | "fail" | "not_evaluated";
export type ReleaseGateStatus = "ready_for_qp_review" | "not_ready";
export type DeskStage =
  | "classified"
  | "sterility_hold"
  | "pending_qp"
  | "qp_recorded";
export type QpAction = "confirm" | "decline" | "cannot_confirm";
export type QpOutcome =
  | "confirmed"
  | "declined"
  | "cannot_confirm"
  | "rejected";
export type ChallengerAttackType =
  | "unsourced_claim"
  | "alternative_cause"
  | "insufficient_evidence"
  | "overconfident";

export type CaseIntake = {
  tenant_id: string;
  batch_id: string;
  product_name: string;
  doc_type: DocType;
  description: string;
  reported_by: string;
  evidence_refs: string[];
  detected_at: string;
  patient_safety_impact: boolean;
  sterility_assurance_impact: boolean;
  product_quality_impact: ProductQualityImpact;
  affects_release_criteria: boolean;
  regulatory_reportable: boolean;
  prior_occurrences_90d: number;
};

export type ClassifiedCase = {
  case_id: string;
  tenant_id: string;
  batch_id: string;
  product_name: string;
  doc_type: DocType;
  severity: DeviationSeverity;
  lifecycle_state: LifecycleState;
  decision_authority: typeof DECISION_AUTHORITY;
  evidence_refs: string[];
  rationale: string;
  classified_at: string;
};

export type SourceAnchor = {
  artifact_filename: string;
  artifact_version: number;
  artifact_sha256: string;
  source_uri: string;
  page_number: number;
  transcribed_context: string;
};

export type ComplianceFinding = {
  finding_id: string;
  finding_type: FindingType;
  field_name: string;
  label: string;
  root_cause_category: RootCauseCategory;
  description: string;
  anchor: SourceAnchor | null;
  observed_value: string | null;
  expected_value: string | null;
  oos_phase: "phase_i_laboratory" | "phase_ii_full_scale" | null;
  sigma_distance: number | null;
};

export type BranchOutcome = {
  department: Department;
  status: BranchStatus;
  error: string | null;
  source_uris: string[];
  findings: ComplianceFinding[];
  decision_authority: typeof DECISION_AUTHORITY;
};

export type CaseMemoryFact = {
  fact_id: string;
  tenant_id: string;
  case_id: string;
  batch_id: string;
  fact: string;
  source_uri: string;
  confidence: number;
  observed_at: string;
  redacted: boolean;
};

export type CitedClaim = {
  claim: string;
  source_uris: string[];
  finding_ids: string[];
};

export type RootCauseHypothesis = {
  category: RootCauseCategory;
  statement: string;
  supporting_claims: CitedClaim[];
  confidence: number;
};

export type InvestigationDraft = {
  tenant_id: string;
  case_id: string;
  batch_id: string;
  hypotheses: RootCauseHypothesis[];
  product_impact: {
    statement: string;
    supporting_claims: CitedClaim[];
    patient_safety_touch: boolean;
  } | null;
  open_questions: string[];
  decision_authority: typeof DECISION_AUTHORITY;
};

export type ChallengerAttack = {
  attack_type: ChallengerAttackType;
  target: string;
  detail: string;
};

export type ChallengerReport = {
  attacks: ChallengerAttack[];
  decision_authority: typeof DECISION_AUTHORITY;
};

export type InvestigationGateResult = {
  passed: boolean;
  blocked_reasons: string[];
  decision_authority: typeof DECISION_AUTHORITY;
};

export type ChecklistItemResult = {
  annex_ref: string;
  title: string;
  status: ChecklistItemStatus;
  evidence_uris: string[];
  reason: string;
  required_for_confirmation: boolean;
};

export type ReleaseGateResult = {
  status: ReleaseGateStatus;
  spec_id: string;
  spec_version: string;
  spec_sha256: string;
  items: ChecklistItemResult[];
  blocked_reasons: string[];
  decision_authority: typeof DECISION_AUTHORITY;
};

export type ExtractedField = {
  field_name: string;
  label: string;
  value: string;
  confidence: number;
  needs_review: boolean;
};

export type QpConfirmationRecord = {
  action: QpAction;
  outcome: QpOutcome;
  qp_name: string;
  rationale: string;
  tenant_id: string;
  case_id: string;
  batch_id: string;
  gate_status: ReleaseGateStatus;
  recorded_at: string;
  rejection_reason: string | null;
  decision_authority: typeof DECISION_AUTHORITY;
};

export type SterilityHold = {
  incubation_days: number;
  started_at: string;
  due_at: string;
  status: "incubating" | "due" | "ticked";
};

export type CfiTriggerReason =
  | "gate_failed"
  | "no_hypotheses"
  | "low_confidence"
  | "challenger_insufficient_evidence"
  | "challenger_alternative_cause"
  | "recurrence";
export type CfiHypothesisState = "open" | "supported" | "refuted" | "undetermined";
export type CfiEnquiryKind = "scope_expansion" | "interview" | "trend_correlation";
export type CfiOutcome =
  | "root_cause_identified"
  | "root_cause_undetermined"
  | "cfi_abandoned_budget_exhausted";
export type CfiLaunchStatus = "started" | "pending" | "launching" | "failed";
export type CfiInterviewStatus =
  | "created"
  | "question_send_pending"
  | "waiting_for_reply"
  | "polling"
  | "reply_captured"
  | "completed"
  | "interview_unanswered"
  | "failed";
export type CfiReplyClassification =
  | "answer"
  | "decline"
  | "stop"
  | "unreadable"
  | "out_of_scope";
export type CfiInterviewMessageKind = "question" | "nudge";

export type CfiBudgetState = {
  planning_rounds_used: number;
  tool_calls_in_current_round: number;
  budget_spec_version: string;
  budget_spec_sha256: string;
};

export type CfiCitedClaim = {
  claim: string;
  evidence_ids: string[];
};

export type CfiHypothesis = {
  hypothesis_id: string;
  category: RootCauseCategory;
  statement: string;
  state: CfiHypothesisState;
  opened_in_round: number;
  supporting_claims: CfiCitedClaim[];
  refuting_claims: CfiCitedClaim[];
  settled_at: string | null;
};

export type CfiEvidenceItem = {
  evidence_id: string;
  round_number: number;
  source_tool: string;
  source_uri: string;
  fact: string;
  captured_at: string;
};

export type CfiSiteReviewReference = {
  calendar_id: string;
  event_id: string;
  event_url: string;
  start_at: string;
  end_at: string;
  attendees: string[];
  decision_authority: typeof DECISION_AUTHORITY;
};

export type CfiReportReference = {
  document_id: string;
  document_url: string;
  was_update: boolean;
  decision_authority: typeof DECISION_AUTHORITY;
};

export type CfiInterviewQuestion = {
  question_number: number;
  kind: CfiInterviewMessageKind;
  question_text: string;
  sent_at: string | null;
  message_name: string | null;
  thread_name: string | null;
  space_name: string | null;
};

export type CfiInterviewReply = {
  message_name: string;
  sender_id: string;
  raw_text: string;
  received_at: string;
  classification: CfiReplyClassification;
  sender_allowed: boolean;
  question_number: number;
};

/** Durable interview shape returned by the CFI interview store. */
export type CfiInterviewRecord = {
  interview_id: string;
  revision: number;
  tenant_id: string;
  cfi_id: string;
  hypothesis_id: string;
  round_number: number;
  status: CfiInterviewStatus;
  allowed_responder_ids: string[];
  space_name: string | null;
  thread_name: string | null;
  questions: CfiInterviewQuestion[];
  replies: CfiInterviewReply[];
  last_seen_message_name: string | null;
  last_seen_message_time: string | null;
  nudge_count: number;
  max_nudges: number;
  deadline_at: string;
  last_nudge_at: string | null;
  poll_lease_until: string | null;
  resume_handle: {
    app_name: string;
    user_id: string;
    session_id: string;
    invocation_id: string;
    interrupt_id: string;
  } | null;
  created_at: string;
  outcome_summary: string | null;
  decision_authority: typeof DECISION_AUTHORITY;
};

/** Reduced interview view used when a live CFI event has only a checkpoint. */
export type CfiInterviewSummary = {
  interview_id: string;
  cfi_id: string;
  hypothesis_id: string | null;
  round_number: number | null;
  status: CfiInterviewStatus;
  question: string | null;
  thread_name: string | null;
  deadline_at: string | null;
  nudge_count: number;
  max_nudges: number | null;
  replies: CfiInterviewReply[];
  outcome_summary: string | null;
  decision_authority: typeof DECISION_AUTHORITY;
};

export type CfiActivity = {
  activity_id: string;
  node: string;
  kind:
    | "handoff"
    | "planning"
    | "dispatch"
    | "interview"
    | "evidence"
    | "closure"
    | "artifact";
  at: string | null;
  detail: string;
  decision_authority: typeof DECISION_AUTHORITY;
};

/** Durable CFI aggregate as projected onto the operator desk. */
export type CfiRecord = {
  cfi_id: string;
  revision: number;
  tenant_id: string;
  source_case_id: string;
  batch_id: string;
  trigger_reasons: CfiTriggerReason[];
  trigger_spec_version: string;
  trigger_spec_sha256: string;
  opened_at: string;
  hypotheses: CfiHypothesis[];
  evidence: CfiEvidenceItem[];
  budget: CfiBudgetState;
  outcome: CfiOutcome | null;
  closed_at: string | null;
  launch_status: CfiLaunchStatus;
  launch_attempts: number;
  launch_lease_until: string | null;
  launch_error: string | null;
  runtime_invocation_id: string | null;
  site_review: CfiSiteReviewReference | null;
  site_review_error: string | null;
  report: CfiReportReference | null;
  report_error: string | null;
  decision_authority: typeof DECISION_AUTHORITY;
};

export type CfiDeskState = CfiRecord & {
  interviews: CfiInterviewSummary[];
  activity: CfiActivity[];
};

export type CaseDeskRecord = {
  data_class: "labelled_synthetic";
  origin: "fixture" | "intake" | "live";
  desk_stage: DeskStage;
  intake: CaseIntake;
  classified: ClassifiedCase;
  aggregated_status: ComplianceStatus | null;
  branches: BranchOutcome[];
  findings: ComplianceFinding[];
  prior_art: CaseMemoryFact[];
  prior_art_embedded: boolean;
  extracted_fields: ExtractedField[];
  draft: InvestigationDraft | null;
  challenger: ChallengerReport | null;
  investigation_gate: InvestigationGateResult | null;
  release_gate: ReleaseGateResult | null;
  sterility_hold: SterilityHold | null;
  qp_confirmation: QpConfirmationRecord | null;
  cfi: CfiDeskState | null;
  decision_authority: typeof DECISION_AUTHORITY;
};

export type CaseQueueItem = {
  case_id: string;
  tenant_id: string;
  batch_id: string;
  product_name: string;
  severity: DeviationSeverity;
  lifecycle_state: LifecycleState;
  desk_stage: DeskStage;
  gate_status: ReleaseGateStatus | null;
  origin: "fixture" | "intake" | "live";
};

/** Live-engine session pointer, kept alongside the record for resume/poll. */
export type LiveSession = {
  engine_resource: string;
  user_id: string;
  session_id: string;
  pending_interrupt: {
    interrupt_id: string;
    function_name: string;
    node: string;
    message: string;
  } | null;
};

export type QpConfirmBody = {
  action: QpAction;
  qp_name: string;
  rationale: string;
};
