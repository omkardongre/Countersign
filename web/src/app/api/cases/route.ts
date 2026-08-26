import { NextResponse } from "next/server";
import { buildClassifiedCase, openFromIntake } from "@/lib/store";
import { listAllCases, liveEngineConfigured } from "@/lib/desk";
import { openLiveCase } from "@/lib/live/live-store";
import type { CaseIntake, DocType, ProductQualityImpact } from "@/lib/types";

const DOC_TYPES: DocType[] = [
  "batch_record",
  "lab_result",
  "supplier_coa",
  "operator_report",
  "environmental_log",
];
const QUALITY: ProductQualityImpact[] = ["none", "potential", "confirmed"];

export function GET() {
  return NextResponse.json({
    cases: listAllCases(),
    live_engine: liveEngineConfigured(),
    data_class: "labelled_synthetic",
  });
}

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  if (!body || typeof body !== "object") {
    return NextResponse.json({ error: "JSON body required." }, { status: 400 });
  }
  const intake = parseIntake(body.intake);
  if ("error" in intake) {
    return NextResponse.json({ error: intake.error }, { status: 400 });
  }

  if (body.source === "live") {
    if (!liveEngineConfigured()) {
      return NextResponse.json(
        {
          error:
            "Live graph is not wired. Set COUNTERSIGN_CASE_ENGINE_URL. Do not hold SSE (15 min max); poll session state.",
        },
        { status: 501 },
      );
    }
    const driveFileId =
      typeof body.drive_file_id === "string" ? body.drive_file_id.trim() : "";
    if (!driveFileId) {
      return NextResponse.json(
        {
          error:
            "drive_file_id is required to open a live case. The manufacturing " +
            "branch pulls the real BMR page from Google Drive over the " +
            "drive_fetch_evidence MCP tool; there is no placeholder evidence.",
        },
        { status: 400 },
      );
    }
    // Same deterministic rule as the intake path, but not persisted here —
    // the live session map owns the record so one live case does not also
    // appear as an in-memory intake case in the queue. The graph's own
    // intake_and_bind_evidence node is authoritative once events land.
    const classified = buildClassifiedCase(intake);
    try {
      const record = await openLiveCase({
        intake,
        classified,
        evidence: {
          // mimeType/contentBase64 are placeholders the Runtime never uses
          // once drive_file_id is set (see app/domain/branches.py); a real
          // MIME type/bytes come back from the drive_fetch_evidence call.
          sourceUri: `drive://${driveFileId}`,
          mimeType: "application/pdf",
          contentBase64: "",
          driveFileId,
        },
      });
      return NextResponse.json(record, { status: 201 });
    } catch (err) {
      return NextResponse.json(
        {
          error:
            err instanceof Error
              ? err.message
              : "Live case open failed against Agent Runtime.",
        },
        { status: 502 },
      );
    }
  }

  const record = openFromIntake(intake);
  return NextResponse.json(record, { status: 201 });
}

function parseIntake(raw: unknown): CaseIntake | { error: string } {
  if (!raw || typeof raw !== "object") {
    return { error: "intake is required." };
  }
  const o = raw as Record<string, unknown>;
  const tenant_id = str(o.tenant_id);
  const batch_id = str(o.batch_id);
  const product_name = str(o.product_name);
  const description = str(o.description);
  const reported_by = str(o.reported_by);
  if (!tenant_id || !batch_id || !product_name || !description || !reported_by) {
    return { error: "tenant_id, batch_id, product_name, description, reported_by are required." };
  }
  if (!DOC_TYPES.includes(o.doc_type as DocType)) {
    return { error: "Invalid doc_type." };
  }
  if (!QUALITY.includes((o.product_quality_impact as ProductQualityImpact) ?? "none")) {
    return { error: "Invalid product_quality_impact." };
  }
  const prior = Number(o.prior_occurrences_90d ?? 0);
  if (!Number.isFinite(prior) || prior < 0) {
    return { error: "prior_occurrences_90d must be >= 0." };
  }
  return {
    tenant_id,
    batch_id,
    product_name,
    doc_type: o.doc_type as DocType,
    description,
    reported_by,
    evidence_refs: Array.isArray(o.evidence_refs)
      ? o.evidence_refs.map(String)
      : [],
    detected_at:
      typeof o.detected_at === "string" ? o.detected_at : new Date().toISOString(),
    patient_safety_impact: Boolean(o.patient_safety_impact),
    sterility_assurance_impact: Boolean(o.sterility_assurance_impact),
    sterility_hold_days: parseHoldDays(o.sterility_hold_days),
    product_quality_impact: (o.product_quality_impact as ProductQualityImpact) ?? "none",
    affects_release_criteria: Boolean(o.affects_release_criteria),
    regulatory_reportable: Boolean(o.regulatory_reportable),
    prior_occurrences_90d: prior,
  };
}

function str(v: unknown) {
  return typeof v === "string" ? v.trim() : "";
}

/** QA-set sterility hold in days; null means the platform default applies. */
function parseHoldDays(v: unknown): number | null {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;
}
