import { NextResponse } from "next/server";
import { getCase } from "@/lib/store";
import { isLiveCase, pollLiveCase } from "@/lib/live/live-store";

/**
 * Polls the live Agent Runtime session on every GET for a live case (`11`
 * §1: poll, never hold an SSE connection open across the sterility wait or
 * any other pause). Fixture/intake cases are unaffected and stay
 * synchronous, same as before this route grew a live branch.
 */
export async function GET(
  _request: Request,
  context: { params: Promise<{ caseId: string }> },
) {
  const { caseId } = await context.params;
  if (isLiveCase(caseId)) {
    try {
      const record = await pollLiveCase(caseId);
      if (!record) {
        return NextResponse.json({ error: "Case not found." }, { status: 404 });
      }
      return NextResponse.json(record);
    } catch (err) {
      return NextResponse.json(
        {
          error:
            err instanceof Error
              ? err.message
              : "Failed to poll the live Agent Runtime session.",
        },
        { status: 502 },
      );
    }
  }
  const record = getCase(caseId);
  if (!record) {
    return NextResponse.json({ error: "Case not found." }, { status: 404 });
  }
  return NextResponse.json(record);
}
