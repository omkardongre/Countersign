import type {
  CaseDeskRecord,
  QpAction,
  QpConfirmationRecord,
  QpOutcome,
} from "./types";

export function recordQpDecision(
  record: CaseDeskRecord,
  request: { action: QpAction; qp_name: string; rationale: string },
  recordedAt = new Date().toISOString(),
): QpConfirmationRecord {
  const gateStatus = record.release_gate?.status ?? "not_ready";
  let outcome: QpOutcome;
  let rejectionReason: string | null = null;

  if (request.action === "confirm") {
    if (gateStatus === "not_ready") {
      outcome = "rejected";
      rejectionReason =
        "Confirmation is refused while the Annex 16 pack is not_ready.";
    } else {
      outcome = "confirmed";
    }
  } else if (request.action === "decline") {
    outcome = "declined";
  } else {
    outcome = "cannot_confirm";
  }

  return {
    action: request.action,
    outcome,
    qp_name: request.qp_name.trim(),
    rationale: request.rationale.trim(),
    tenant_id: record.classified.tenant_id,
    case_id: record.classified.case_id,
    batch_id: record.classified.batch_id,
    gate_status: gateStatus,
    recorded_at: recordedAt,
    rejection_reason: rejectionReason,
    decision_authority: "none",
  };
}
