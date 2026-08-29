import type {
  CaseDeskRecord,
  CfiDeskState,
  ChecklistItemResult,
  ComplianceFinding,
} from "./types";

// Evidence URIs are derived per batch. A citation must resolve to the batch it
// is filed against — ARCHITECTURE.md: "Every claim carries its source." A
// fixture that cited another batch's BMR page would show a QP evidence for a
// batch they are not reviewing, so nothing here is shared across batches.
const bmrUri = (batch: string) => `drive://synthetic/bmr/${batch}/page-14`;
const limsUri = (batch: string) => `cloudsql://synthetic/lims/${batch}`;
const emUri = (batch: string) => `bigquery://synthetic/em/${batch}`;
const coaUri = (batch: string) => `drive://synthetic/coa/${batch}`;

const SPEC_SHA =
  "labelled-synthetic-annex16-v1-not-a-live-hash";

/** The blend-speed OOT finding, anchored to the batch's own BMR page. */
function blendFinding(batch: string): ComplianceFinding {
  return {
    finding_id: `fnd-${batch.toLowerCase().replace("-", "")}-blend`,
    finding_type: "out_of_trend",
    field_name: "blend_speed_rpm",
    label: "Blend speed",
    root_cause_category: "method",
    description:
      "Blend speed 48 rpm sits inside specification (40–60) but is 3.1σ from the AXR450 trend baseline.",
    anchor: {
      artifact_filename: `${batch}-bmr-page-14.png`,
      artifact_version: 1,
      artifact_sha256: "synthetic-page-sha256",
      source_uri: bmrUri(batch),
      page_number: 14,
      transcribed_context: "Blend speed: 48 rpm  (spec 40–60)",
    },
    observed_value: "48 rpm",
    expected_value: "trend mean 52.4 rpm ± 1.2",
    oos_phase: null,
    sigma_distance: 3.1,
  };
}

function annex(
  ref: string,
  title: string,
  status: ChecklistItemResult["status"],
  reason: string,
  required: boolean,
  uris: string[] = [],
): ChecklistItemResult {
  return {
    annex_ref: ref,
    title,
    status,
    evidence_uris: uris,
    reason,
    required_for_confirmation: required,
  };
}

const unevaluated = (ref: string, title: string) =>
  annex(
    ref,
    title,
    "not_evaluated",
    "No evaluator is bound on annex16_checklist_v1.json; stays not_evaluated.",
    false,
  );

function annexChecklist(batch: string, ready: boolean): ChecklistItemResult[] {
  return [
    unevaluated("1.7.1", "Manufacture and testing conducted in accordance with GMP."),
    unevaluated(
      "1.7.2",
      "Supply chain of the active substance and product documented for the QP.",
    ),
    unevaluated(
      "1.7.3",
      "Audits of manufacture and testing sites performed; reports available.",
    ),
    unevaluated(
      "1.7.4",
      "Sites of manufacture, analysis and certification compliant with the MA.",
    ),
    unevaluated(
      "1.7.5",
      "Manufacturing and testing activities consistent with the MA.",
    ),
    annex(
      "1.7.6",
      "Starting and packaging material sources and specifications as in the MA.",
      "pass",
      "Supply branch collected; no materials findings.",
      true,
      [coaUri(batch)],
    ),
    unevaluated(
      "1.7.7",
      "Active substances manufactured in accordance with GMP and GDP where required.",
    ),
    unevaluated(
      "1.7.8",
      "API importation complies with Directive 2001/83/EC Article 46(b).",
    ),
    unevaluated(
      "1.7.9",
      "Excipients manufactured in accordance with ascertained GMP.",
    ),
    unevaluated(
      "1.7.10",
      "TSE status of materials compliant with the MA where relevant.",
    ),
    annex(
      "1.7.11",
      "Records complete and endorsed; required in-process controls made.",
      "pass",
      "Manufacturing branch collected; no completeness findings.",
      true,
      [bmrUri(batch)],
    ),
    unevaluated(
      "1.7.12",
      "Manufacturing and testing processes remain validated; personnel qualified.",
    ),
    annex(
      "1.7.13",
      "Finished-product QC data complies with the MA specification or authorised RTRT.",
      "pass",
      "QC branch collected; no OOS findings.",
      true,
      [limsUri(batch)],
    ),
    unevaluated(
      "1.7.14",
      "Post-marketing commitments addressed; ongoing stability supports certification.",
    ),
    unevaluated(
      "1.7.15",
      "Impact of manufacturing or testing changes evaluated; extra checks complete.",
    ),
    annex(
      "1.7.16",
      "Investigations for the batch, including OOS and OOT, complete enough to support certification.",
      ready ? "pass" : "fail",
      ready
        ? "Investigation gate passed; OOT finding cited on the BMR page."
        : "Open investigation remains on the batch (OOT blend speed).",
      true,
      [bmrUri(batch)],
    ),
    annex(
      "1.7.17",
      "Ongoing complaints, investigations or recalls do not negate conditions for this batch.",
      ready ? "pass" : "fail",
      ready
        ? "No open batch issues remain on the joined case."
        : `This deviation is still open against ${batch}.`,
      true,
    ),
    annex(
      "1.7.18",
      "Required technical agreements are in place.",
      ready ? "pass" : "not_evaluated",
      ready
        ? "TQA Aurex/Countersign bound for this tenant (synthetic)."
        : "Technical-agreement evaluator has no live TQA row on this snapshot.",
      true,
    ),
    unevaluated(
      "1.7.19",
      "Self-inspection programme is active and current.",
    ),
    unevaluated(
      "1.7.20",
      "Appropriate arrangements for distribution and shipment are in place.",
    ),
    unevaluated(
      "1.7.21",
      "Safety features affixed where required by Directive 2001/83/EC Article 54(o).",
    ),
  ];
}

/**
 * The shared shape of an AXR450 blend-speed case, built per batch.
 *
 * Deliberately a factory rather than one object spread across batches: every
 * evidence URI, finding id, and cited claim has to name the batch it belongs
 * to. Spreading a single literal silently gave one batch another batch's
 * citations, which is exactly what the citation gate exists to prevent.
 */
function caseBase(batch: string) {
  const finding = blendFinding(batch);
  const bmr = bmrUri(batch);
  const caseId = `case-${batch.toLowerCase()}`;

  return {
    data_class: "labelled_synthetic" as const,
    intake: {
      tenant_id: "aurex",
      batch_id: batch,
      product_name: "AXR450 Sterile Injectable",
      doc_type: "batch_record" as const,
      description: `Blend-speed excursion recorded on BMR page 14 during compounding of ${batch}.`,
      reported_by: "M. Chen (compounding, synthetic)",
      detected_at: "2026-08-12T09:14:00.000Z",
      patient_safety_impact: false,
      sterility_assurance_impact: true,
      sterility_hold_seconds: 14 * 24 * 60 * 60,
      product_quality_impact: "potential" as const,
      affects_release_criteria: true,
      regulatory_reportable: false,
      prior_occurrences_90d: 1,
    },
    aggregated_status: "sufficient_for_investigation" as const,
    branches: [
      {
        department: "manufacturing" as const,
        status: "ok" as const,
        error: null,
        source_uris: [bmr],
        findings: [finding],
        decision_authority: "none" as const,
      },
      {
        department: "qc" as const,
        status: "ok" as const,
        error: null,
        source_uris: [limsUri(batch)],
        findings: [],
        decision_authority: "none" as const,
      },
      {
        department: "environmental_monitoring" as const,
        status: "ok" as const,
        error: null,
        source_uris: [emUri(batch)],
        findings: [],
        decision_authority: "none" as const,
      },
      {
        department: "supply" as const,
        status: "ok" as const,
        error: null,
        source_uris: [coaUri(batch)],
        findings: [],
        decision_authority: "none" as const,
      },
    ],
    findings: [finding],
    prior_art: [
      {
        fact_id: "mem-aurex-2188",
        tenant_id: "aurex",
        case_id: "case-ax-2188",
        batch_id: "AX-2188",
        fact: "Prior AXR450 compounding OOT on blend speed was closed as method (set-point drift on mixer M-12).",
        source_uri: "qms://synthetic/deviations/AX-2188",
        confidence: 0.81,
        observed_at: "2026-05-03T16:00:00.000Z",
        redacted: false,
      },
    ],
    prior_art_embedded: true,
    extracted_fields: [
      {
        field_name: "batch_id",
        label: "Batch",
        value: batch,
        confidence: 0.99,
        needs_review: false,
      },
      {
        field_name: "blend_speed_rpm",
        label: "Blend speed",
        value: "48 rpm",
        confidence: 0.71,
        needs_review: true,
      },
      {
        field_name: "operator_signoff",
        label: "Operator sign-off",
        value: "M. Chen",
        confidence: 0.94,
        needs_review: false,
      },
    ],
    draft: {
      tenant_id: "aurex",
      case_id: caseId,
      batch_id: batch,
      hypotheses: [
        {
          category: "method" as const,
          statement:
            "Blend-speed excursion on the BMR is the leading process deviation to investigate.",
          supporting_claims: [
            {
              claim: "Manufacturing ingested BMR page 14 with blend speed 48 rpm.",
              source_uris: [bmr],
              finding_ids: [finding.finding_id],
            },
          ],
          confidence: 0.62,
        },
      ],
      product_impact: {
        statement:
          "Patient-safety impact is not determined by this draft. Sterility-assurance impact is on the classified case.",
        supporting_claims: [
          {
            claim: "Sterility-assurance impact was set at intake, not inferred.",
            source_uris: [bmr],
            finding_ids: [],
          },
        ],
        patient_safety_touch: false,
      },
      open_questions: [
        "Was mixer M-12 set-point verified after the previous AX-2188 close-out?",
      ],
      decision_authority: "none" as const,
    },
    challenger: {
      attacks: [
        {
          attack_type: "alternative_cause" as const,
          target: "method / mixer set-point",
          detail:
            "A measurement error on the tachometer would also explain 48 rpm without a process change.",
        },
      ],
      decision_authority: "none" as const,
    },
    cfi: null,
    decision_authority: "none" as const,
  };
}

const ax2291Base = caseBase("AX-2291");
const ax2402Base = caseBase("AX-2402");

const ax2402Cfi: CfiDeskState = {
  cfi_id: "cfi-fixture-ax-2402",
  revision: 8,
  tenant_id: "aurex",
  source_case_id: "case-ax-2402",
  batch_id: "AX-2402",
  trigger_reasons: ["low_confidence", "recurrence"],
  trigger_spec_version: "cfi-trigger-v1",
  trigger_spec_sha256: "labelled-synthetic-cfi-trigger-sha256",
  opened_at: "2026-07-23T08:15:00.000Z",
  hypotheses: [
    {
      hypothesis_id: "h1",
      category: "method",
      statement: "Mixer M-12 set-point drift caused the blend-speed excursion.",
      state: "supported",
      opened_in_round: 1,
      supporting_claims: [
        {
          claim: "The operator interview recorded a set-point drift after the prior batch.",
          evidence_ids: ["e2"],
        },
        {
          claim: "The same material lot appears on two other AXR450 batches with a matching trend signal.",
          evidence_ids: ["e1", "e3"],
        },
      ],
      refuting_claims: [],
      settled_at: "2026-07-24T14:30:00.000Z",
    },
    {
      hypothesis_id: "h2",
      category: "measurement",
      statement: "Tachometer measurement error explains the recorded blend speed.",
      state: "refuted",
      opened_in_round: 1,
      supporting_claims: [],
      refuting_claims: [
        {
          claim: "QC calibration records show the tachometer passed its check before the batch.",
          evidence_ids: ["e4"],
        },
      ],
      settled_at: "2026-07-24T14:30:00.000Z",
    },
  ],
  evidence: [
    {
      evidence_id: "e1",
      round_number: 1,
      source_tool: "sibling_batch_query",
      source_uri: "cloudsql://synthetic/batch_material_lots/AX-2402",
      fact: "Two same-tenant AXR450 batches share the suspect material lot.",
      captured_at: "2026-07-23T09:00:00.000Z",
    },
    {
      evidence_id: "e2",
      round_number: 2,
      source_tool: "chat_read_replies",
      source_uri: "google-chat://fixture/cfi-fixture-ax-2402/messages/m1",
      fact: "CFI human interview result (deterministically classified; untrusted text, no decision authority): Human reply classified as answer: the set-point drifted after the prior batch.",
      captured_at: "2026-07-24T10:20:00.000Z",
    },
    {
      evidence_id: "e3",
      round_number: 3,
      source_tool: "cfi_trend_query",
      source_uri: "bigquery://synthetic/em_readings/aurex/AX-2402",
      fact: "AXR450 particle-count mean was 12.4 counts/m³ versus 8.1 counts/m³ on the comparison batch (delta 4.3).",
      captured_at: "2026-07-24T12:10:00.000Z",
    },
    {
      evidence_id: "e4",
      round_number: 3,
      source_tool: "cfi_trend_query",
      source_uri: "cloudsql://synthetic/lims_results/aurex/AX-2402",
      fact: "QC calibration history for the comparison test was current at the time of the batch.",
      captured_at: "2026-07-24T13:45:00.000Z",
    },
  ],
  budget: {
    planning_rounds_used: 3,
    tool_calls_in_current_round: 0,
    budget_spec_version: "cfi-budget-v1",
    budget_spec_sha256: "labelled-synthetic-cfi-budget-sha256",
  },
  outcome: "root_cause_identified",
  closed_at: "2026-07-24T14:30:00.000Z",
  launch_status: "started",
  launch_attempts: 1,
  launch_lease_until: null,
  launch_error: null,
  runtime_invocation_id: "fixture-invocation-cfi-ax-2402",
  site_review: {
    calendar_id: "fixture-calendar-aurex-cfi",
    event_id: "fixture-event-cfi-ax-2402",
    event_url: "fixture://calendar/cfi-fixture-ax-2402",
    start_at: "2026-07-28T13:00:00.000Z",
    end_at: "2026-07-28T14:00:00.000Z",
    attendees: ["fixture://users/qa-lead", "fixture://users/site-quality"],
    decision_authority: "none",
  },
  site_review_error: null,
  report: {
    document_id: "fixture-document-cfi-ax-2402",
    document_url: "fixture://drive/cfi-report-ax-2402",
    was_update: false,
    decision_authority: "none",
  },
  report_error: null,
  decision_authority: "none",
  interviews: [
    {
      interview_id: "cfi-interview-fixture-ax-2402",
      cfi_id: "cfi-fixture-ax-2402",
      hypothesis_id: "h1",
      round_number: 2,
      status: "completed",
      question: "What changed on mixer M-12 after the previous AXR450 batch?",
      thread_name: "fixture://chat/threads/cfi-ax-2402",
      deadline_at: "2026-07-24T12:00:00.000Z",
      nudge_count: 0,
      max_nudges: 2,
      replies: [
        {
          message_name: "fixture-message-cfi-ax-2402",
          sender_id: "fixture://users/operator",
          raw_text: "The set-point drifted after the prior batch; maintenance corrected it before the next run.",
          received_at: "2026-07-24T10:20:00.000Z",
          classification: "answer",
          sender_allowed: true,
          question_number: 1,
        },
      ],
      outcome_summary: "Answer captured and classified deterministically.",
      decision_authority: "none",
    },
  ],
  activity: [
    {
      activity_id: "fixture-cfi-activity-1",
      node: "load_cfi",
      kind: "handoff",
      at: "2026-07-23T08:15:00.000Z",
      detail: "Durable CFI opened from case-ax-2402.",
      decision_authority: "none",
    },
    {
      activity_id: "fixture-cfi-activity-2",
      node: "cfi_plan_round",
      kind: "planning",
      at: "2026-07-23T08:20:00.000Z",
      detail: "Lead proposed the mixer set-point hypothesis and a scope-expansion enquiry.",
      decision_authority: "none",
    },
    {
      activity_id: "fixture-cfi-activity-3",
      node: "run_scope_expansion",
      kind: "dispatch",
      at: "2026-07-23T09:00:00.000Z",
      detail: "Sibling-batch association captured from the tenant-locked query.",
      decision_authority: "none",
    },
    {
      activity_id: "fixture-cfi-activity-4",
      node: "run_interview",
      kind: "interview",
      at: "2026-07-24T10:20:00.000Z",
      detail: "Allowed responder reply captured and classified as an answer.",
      decision_authority: "none",
    },
    {
      activity_id: "fixture-cfi-activity-5",
      node: "cfi_closure_node",
      kind: "closure",
      at: "2026-07-24T14:30:00.000Z",
      detail: "All hypotheses settled; outcome root_cause_identified.",
      decision_authority: "none",
    },
    {
      activity_id: "fixture-cfi-activity-6",
      node: "schedule_site_review",
      kind: "artifact",
      at: "2026-07-24T14:31:00.000Z",
      detail: "Fixture site-review event reference recorded.",
      decision_authority: "none",
    },
    {
      activity_id: "fixture-cfi-activity-7",
      node: "file_cfi_report",
      kind: "artifact",
      at: "2026-07-24T14:32:00.000Z",
      detail: "Fixture report reference recorded; content remains deterministic.",
      decision_authority: "none",
    },
  ],
};

export const SEED_CASES: CaseDeskRecord[] = [
  {
    ...ax2291Base,
    origin: "fixture",
    desk_stage: "pending_qp",
    classified: {
      case_id: "case-ax-2291",
      tenant_id: "aurex",
      batch_id: "AX-2291",
      product_name: "AXR450 Sterile Injectable",
      doc_type: "batch_record",
      sterility_hold_seconds: 14 * 24 * 60 * 60,
      severity: "critical",
      lifecycle_state: "pending_qp",
      decision_authority: "none",
      rationale: "sterility_assurance_impact=true",
      classified_at: "2026-08-12T09:14:12.000Z",
    },
    investigation_gate: {
      passed: true,
      blocked_reasons: [],
      decision_authority: "none",
    },
    release_gate: {
      status: "not_ready",
      spec_id: "annex16_qp_checklist",
      spec_version: "1.0.0",
      spec_sha256: SPEC_SHA,
      items: annexChecklist("AX-2291", false),
      blocked_reasons: [
        "1.7.16 investigations remain open on the batch.",
        "1.7.17 this deviation is still open against AX-2291.",
        "1.7.18 technical agreement not evaluated on this snapshot.",
      ],
      decision_authority: "none",
    },
    sterility_hold: null,
    qp_confirmation: null,
  },
  {
    ...ax2402Base,
    origin: "fixture",
    desk_stage: "pending_qp",
    intake: {
      ...ax2402Base.intake,
      description:
        "Closed investigation pack for AX-2402 (labelled synthetic). Same product family as AX-2291.",
    },
    classified: {
      case_id: "case-ax-2402",
      tenant_id: "aurex",
      batch_id: "AX-2402",
      product_name: "AXR450 Sterile Injectable",
      doc_type: "batch_record",
      sterility_hold_seconds: 14 * 24 * 60 * 60,
      severity: "critical",
      lifecycle_state: "pending_qp",
      decision_authority: "none",
      rationale: "sterility_assurance_impact=true",
      classified_at: "2026-07-22T11:02:00.000Z",
    },
    draft: {
      ...ax2402Base.draft,
      open_questions: [],
    },
    challenger: {
      attacks: [],
      decision_authority: "none",
    },
    investigation_gate: {
      passed: true,
      blocked_reasons: [],
      decision_authority: "none",
    },
    release_gate: {
      status: "ready_for_qp_review",
      spec_id: "annex16_qp_checklist",
      spec_version: "1.0.0",
      spec_sha256: SPEC_SHA,
      items: annexChecklist("AX-2402", true),
      blocked_reasons: [],
      decision_authority: "none",
    },
    sterility_hold: null,
    qp_confirmation: null,
    cfi: ax2402Cfi,
  },
  {
    data_class: "labelled_synthetic",
    origin: "fixture",
    desk_stage: "sterility_hold",
    intake: {
      tenant_id: "helix",
      batch_id: "HX-104",
      product_name: "HLX12 Lyophilized Vial",
      doc_type: "lab_result",
      description:
        "Media-fill adjacent sterility sample placed on 14-day incubation after fill-line intervention.",
      reported_by: "R. Okonkwo (QC micro, synthetic)",
      detected_at: "2026-08-04T06:40:00.000Z",
      patient_safety_impact: false,
      sterility_assurance_impact: true,
      sterility_hold_seconds: 14 * 24 * 60 * 60,
      product_quality_impact: "potential",
      affects_release_criteria: true,
      regulatory_reportable: false,
      prior_occurrences_90d: 0,
    },
    classified: {
      case_id: "case-hx-104",
      tenant_id: "helix",
      batch_id: "HX-104",
      product_name: "HLX12 Lyophilized Vial",
      doc_type: "lab_result",
      sterility_hold_seconds: 14 * 24 * 60 * 60,
      severity: "critical",
      lifecycle_state: "under_investigation",
      decision_authority: "none",
      rationale: "sterility_assurance_impact=true",
      classified_at: "2026-08-04T06:40:08.000Z",
    },
    aggregated_status: "sufficient_for_investigation",
    branches: [
      {
        department: "qc",
        status: "ok",
        error: null,
        source_uris: ["cloudsql://synthetic/lims/HX-104"],
        findings: [],
        decision_authority: "none",
      },
    ],
    findings: [],
    prior_art: [],
    prior_art_embedded: false,
    extracted_fields: [
      {
        field_name: "sample_id",
        label: "Sterility sample",
        value: "HX-104-ST-01",
        confidence: 0.97,
        needs_review: false,
      },
    ],
    draft: {
      tenant_id: "helix",
      case_id: "case-hx-104",
      batch_id: "HX-104",
      hypotheses: [
        {
          category: "environment",
          statement:
            "Fill-line intervention is the event under investigation; sterility result is not yet available.",
          supporting_claims: [
            {
              claim: "QC ingested the sterility sample row for HX-104.",
              source_uris: ["cloudsql://synthetic/lims/HX-104"],
              finding_ids: [],
            },
          ],
          confidence: 0.4,
        },
      ],
      product_impact: {
        statement: "Product impact cannot be drafted until incubation completes.",
        supporting_claims: [
          {
            claim: "14-day sterility incubation is in progress.",
            source_uris: ["cloudsql://synthetic/lims/HX-104"],
            finding_ids: [],
          },
        ],
        patient_safety_touch: false,
      },
      open_questions: ["Await sterility incubation tick (14 days)."],
      decision_authority: "none",
    },
    challenger: { attacks: [], decision_authority: "none" },
    investigation_gate: {
      passed: false,
      blocked_reasons: ["sterility incubation incomplete"],
      decision_authority: "none",
    },
    release_gate: null,
    sterility_hold: {
      incubation_days: 14,
      started_at: "2026-08-04T06:41:00.000Z",
      due_at: "2026-08-18T06:41:00.000Z",
      status: "incubating",
    },
    qp_confirmation: null,
    cfi: null,
    decision_authority: "none",
  },
];
