import { notFound } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { CasePanels } from "@/components/case-panels";
import { CfiPanel } from "@/components/cfi-panel";
import { LivePoller } from "@/components/live-poller";
import { Pipeline } from "@/components/pipeline";
import { QpPanel } from "@/components/qp-panel";
import { GatePill, SeverityPill, StagePill } from "@/components/status-pill";
import { getAnyCase } from "@/lib/desk";

export const dynamic = "force-dynamic";

export default async function CasePage({
  params,
}: {
  params: Promise<{ caseId: string }>;
}) {
  const { caseId } = await params;
  const record = getAnyCase(caseId);
  if (!record) notFound();
  const { classified } = record;
  // A live case is only "settled" once the QP has acted. Until then the
  // graph is either running or parked on an interrupt, so the desk polls
  // rather than holding a stream open (final_reseach/11 §1).
  const shouldPoll =
    record.origin === "live" && record.desk_stage !== "qp_recorded";

  return (
    <AppShell current="/">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="font-mono text-[12px] text-ink-muted">
            {classified.tenant_id} · {classified.case_id}
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            {classified.batch_id}
          </h1>
          <p className="text-sm text-ink-muted">{classified.product_name}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SeverityPill value={classified.severity} />
          <StagePill value={record.desk_stage} />
          <GatePill value={record.release_gate?.status ?? null} />
          {record.origin === "live" && (
            <span className="inline-flex items-center rounded-sm bg-teal/10 px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide text-teal-deep ring-1 ring-teal/25">
              live runtime
            </span>
          )}
        </div>
      </div>
      {shouldPoll && (
        <LivePoller caseId={classified.case_id} initialStage={record.desk_stage} />
      )}
      <Pipeline stage={record.desk_stage} lifecycle={classified.lifecycle_state} />
      <div className="mt-5 space-y-4">
        <CfiPanel record={record} />
        <QpPanel record={record} />
        <CasePanels record={record} />
      </div>
    </AppShell>
  );
}
