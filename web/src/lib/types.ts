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
export type Department = "manufacturing" | "qc" | "supply";
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

export type CaseDeskRecord = {
  data_class: "labelled_synthetic";
  origin: "fixture" | "intake";
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
  origin: "fixture" | "intake";
};

export type QpConfirmBody = {
  action: QpAction;
  qp_name: string;
  rationale: string;
};
