import { NextResponse } from "next/server";
import { confirmCase } from "@/lib/store";
import type { QpAction } from "@/lib/types";

const ACTIONS: QpAction[] = ["confirm", "decline", "cannot_confirm"];

export async function POST(
  request: Request,
  context: { params: Promise<{ caseId: string }> },
) {
  const { caseId } = await context.params;
  const body = await request.json().catch(() => null);
  if (!body || typeof body !== "object") {
    return NextResponse.json({ error: "JSON body required." }, { status: 400 });
  }
  if (!ACTIONS.includes(body.action as QpAction)) {
    return NextResponse.json({ error: "Invalid action." }, { status: 400 });
  }
  const result = confirmCase(caseId, {
    action: body.action,
    qp_name: String(body.qp_name ?? ""),
    rationale: String(body.rationale ?? ""),
  });
  if ("error" in result) {
    return NextResponse.json({ error: result.error }, { status: result.status });
  }
  return NextResponse.json(result.record);
}
