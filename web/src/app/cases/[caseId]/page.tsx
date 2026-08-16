import { notFound } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { CasePanels } from "@/components/case-panels";
import { Pipeline } from "@/components/pipeline";
import { QpPanel } from "@/components/qp-panel";
import { GatePill, SeverityPill, StagePill } from "@/components/status-pill";
import { getCase } from "@/lib/store";

export const dynamic = "force-dynamic";

export default async function CasePage({
  params,
}: {
  params: Promise<{ caseId: string }>;
}) {
  const { caseId } = await params;
  const record = getCase(caseId);
  if (!record) notFound();
  const { classified } = record;

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
        <div className="flex flex-wrap gap-2">
          <SeverityPill value={classified.severity} />
          <StagePill value={record.desk_stage} />
          <GatePill value={record.release_gate?.status ?? null} />
        </div>
      </div>
      <Pipeline stage={record.desk_stage} lifecycle={classified.lifecycle_state} />
      <div className="mt-5 space-y-4">
        <QpPanel record={record} />
        <CasePanels record={record} />
      </div>
    </AppShell>
  );
}
