import { NextResponse } from "next/server";
import { confirmCase } from "@/lib/store";
import { getLiveSessionPointer, resumeLiveCase } from "@/lib/live/live-store";
import type { QpAction } from "@/lib/types";

const ACTIONS: QpAction[] = ["confirm", "decline", "cannot_confirm"];
// Literal node name from app/domain/qp_confirmation.py's confirm_qp @node.
const QP_NODE_NAME = "confirm_qp";

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
  const qp_name = String(body.qp_name ?? "").trim();
  const rationale = String(body.rationale ?? "").trim();
  if (!qp_name || !rationale) {
    return NextResponse.json(
      { error: "qp_name and rationale are required." },
      { status: 400 },
    );
  }

  const live = getLiveSessionPointer(caseId);
  if (live) {
    if (!live.pending_interrupt || live.pending_interrupt.node !== QP_NODE_NAME) {
      return NextResponse.json(
        {
          error:
            "QP confirmation is only available while the graph is paused at confirm_qp.",
        },
        { status: 409 },
      );
    }
    try {
      const record = await resumeLiveCase(caseId, {
        action: body.action,
        qp_name,
        rationale,
      });
      return NextResponse.json(record);
    } catch (err) {
      return NextResponse.json(
        {
          error:
            err instanceof Error
              ? err.message
              : "Failed to resume the live Agent Runtime session.",
        },
        { status: 502 },
      );
    }
  }

  const result = confirmCase(caseId, { action: body.action, qp_name, rationale });
  if ("error" in result) {
    return NextResponse.json({ error: result.error }, { status: result.status });
  }
  return NextResponse.json(result.record);
}
