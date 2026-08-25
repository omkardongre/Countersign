import { classifySeverity } from "./classify";
import { SEED_CASES } from "./fixtures";
import { recordQpDecision } from "./qp";
import type {
  CaseDeskRecord,
  CaseIntake,
  CaseQueueItem,
  ClassifiedCase,
  QpConfirmBody,
} from "./types";

type GlobalStore = {
  cases: Map<string, CaseDeskRecord>;
};

function globalStore(): GlobalStore {
  const g = globalThis as typeof globalThis & { __countersignDesk?: GlobalStore };
  if (!g.__countersignDesk) {
    g.__countersignDesk = {
      cases: new Map(SEED_CASES.map((c) => [c.classified.case_id, structuredClone(c)])),
    };
  }
  return g.__countersignDesk;
}

export function listCases(): CaseQueueItem[] {
  return [...globalStore().cases.values()]
    .map(toQueueItem)
    .sort((a, b) => a.batch_id.localeCompare(b.batch_id));
}

export function getCase(caseId: string): CaseDeskRecord | null {
  return globalStore().cases.get(caseId) ?? null;
}

/**
 * Builds the desk's classified record without persisting it.
 *
 * Shared by the intake path (which then stores it here) and the live path
 * (which stores it in the live-session map instead, so a live case does not
 * also appear as an in-memory intake case). `case_id` must satisfy
 * `app/domain/ingest.py`'s `_SAFE_SCOPE_ID` because it becomes part of the
 * evidence artifact filename on the graph side.
 */
export function buildClassifiedCase(intake: CaseIntake): ClassifiedCase {
  const { severity, rationale } = classifySeverity(intake);
  const caseId = `case-${intake.tenant_id}-${intake.batch_id}-${Date.now()}`
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "-");
  return {
    case_id: caseId,
    tenant_id: intake.tenant_id,
    batch_id: intake.batch_id,
    product_name: intake.product_name,
    doc_type: intake.doc_type,
    severity,
    lifecycle_state: "classified",
    decision_authority: "none",
    evidence_refs: intake.evidence_refs,
    rationale,
    classified_at: new Date().toISOString(),
  };
}

/** Empty desk record around an already-classified case. */
export function emptyDeskRecord(
  intake: CaseIntake,
  classified: ClassifiedCase,
  origin: CaseDeskRecord["origin"],
): CaseDeskRecord {
  return {
    data_class: "labelled_synthetic",
    origin,
    desk_stage: "classified",
    intake,
    classified,
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
}

export function openFromIntake(intake: CaseIntake): CaseDeskRecord {
  const classified = buildClassifiedCase(intake);
  const record = emptyDeskRecord(intake, classified, "intake");
  globalStore().cases.set(classified.case_id, record);
  return record;
}

export function confirmCase(
  caseId: string,
  body: QpConfirmBody,
): { record: CaseDeskRecord } | { error: string; status: number } {
  const current = getCase(caseId);
  if (!current) {
    return { error: "Case not found.", status: 404 };
  }
  if (current.desk_stage !== "pending_qp") {
    return {
      error: "QP confirmation is only available at the pending_qp pause.",
      status: 409,
    };
  }
  if (!current.release_gate) {
    return { error: "No Annex 16 pack on this case.", status: 409 };
  }
  const qp_name = body.qp_name.trim();
  const rationale = body.rationale.trim();
  if (!qp_name || !rationale) {
    return { error: "qp_name and rationale are required.", status: 400 };
  }
  const qp = recordQpDecision(current, {
    action: body.action,
    qp_name,
    rationale,
  });
  const next: CaseDeskRecord = {
    ...current,
    qp_confirmation: qp,
    desk_stage: "qp_recorded",
    classified: {
      ...current.classified,
      lifecycle_state:
        qp.outcome === "rejected" ? "pending_qp" : "dispositioned",
    },
  };
  if (qp.outcome === "rejected") {
    next.desk_stage = "pending_qp";
  }
  globalStore().cases.set(caseId, next);
  return { record: next };
}

export function toQueueItem(record: CaseDeskRecord): CaseQueueItem {
  return {
    case_id: record.classified.case_id,
    tenant_id: record.classified.tenant_id,
    batch_id: record.classified.batch_id,
    product_name: record.classified.product_name,
    severity: record.classified.severity,
    lifecycle_state: record.classified.lifecycle_state,
    desk_stage: record.desk_stage,
    gate_status: record.release_gate?.status ?? null,
    origin: record.origin,
  };
}

export function liveEngineConfigured(): boolean {
  return Boolean(process.env.COUNTERSIGN_CASE_ENGINE_URL);
}
