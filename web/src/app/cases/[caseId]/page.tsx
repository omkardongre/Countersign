import Link from "next/link";
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
  const shouldPoll =
    record.origin === "live" && record.desk_stage !== "qp_recorded";

  return (
    <AppShell current="/">
      {shouldPoll && (
        <LivePoller caseId={classified.case_id} initialStage={record.desk_stage} />
      )}

      {/* Breadcrumb */}
      <div className="mb-5">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-[13px] text-ink-muted hover:text-ink transition-colors"
        >
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
          </svg>
          Case Queue
        </Link>
      </div>

      {/* Case header */}
      <div className="mb-6 rounded-xl border border-line bg-paper-raised p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-widest text-ink-muted">
              {classified.tenant_id} · {classified.case_id}
            </p>
            <h1 className="mt-1.5 text-2xl font-bold tracking-tight text-ink">
              {classified.batch_id}
            </h1>
            <p className="mt-0.5 text-sm text-ink-muted">{classified.product_name}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SeverityPill value={classified.severity} />
            <StagePill value={record.desk_stage} />
            <GatePill value={record.release_gate?.status ?? null} />
            {record.origin === "live" && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-minor-bg px-2.5 py-0.5 font-mono text-[11px] font-semibold text-minor">
                <span className="h-1.5 w-1.5 rounded-full bg-minor" />
                LIVE
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Pipeline stepper */}
      <div className="mb-6">
        <Pipeline stage={record.desk_stage} lifecycle={classified.lifecycle_state} />
      </div>

      {/* Content panels */}
      <div className="space-y-5">
        <CfiPanel record={record} />
        <QpPanel record={record} />
        <CasePanels record={record} />
      </div>
    </AppShell>
  );
}
