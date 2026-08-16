import { NextResponse } from "next/server";
import { getCase } from "@/lib/store";

export function GET(
  _request: Request,
  context: { params: Promise<{ caseId: string }> },
) {
  return context.params.then(({ caseId }) => {
    const record = getCase(caseId);
    if (!record) {
      return NextResponse.json({ error: "Case not found." }, { status: 404 });
    }
    return NextResponse.json(record);
  });
}
