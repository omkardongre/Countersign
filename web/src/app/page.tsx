import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { GatePill, SeverityPill, StagePill } from "@/components/status-pill";
import { listCases, liveEngineConfigured } from "@/lib/store";

export const dynamic = "force-dynamic";

export default function Home() {
  const cases = listCases();
  const live = liveEngineConfigured();

  return (
    <AppShell current="/">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Case queue</h1>
          <p className="mt-1 max-w-xl text-sm text-ink-muted">
            Review classified deviations, citations, and the Annex 16 pack.
            Nothing here is a batch release.
          </p>
        </div>
        <Link
          href="/open"
          className="bg-teal-deep px-4 py-2 text-sm text-paper-raised"
        >
          Open case
        </Link>
      </div>
      <p className="mb-4 font-mono text-[12px] text-ink-muted">
        Live Runtime {live ? "env present" : "not wired"} · queue is labelled
        synthetic
      </p>
      <div className="overflow-x-auto border border-line bg-paper-raised">
        <table className="w-full min-w-[44rem] text-left text-sm">
          <thead className="border-b border-line text-[11px] uppercase tracking-wide text-ink-muted">
            <tr>
              <th className="px-4 py-2.5 font-medium">Tenant</th>
              <th className="px-4 py-2.5 font-medium">Batch</th>
              <th className="px-4 py-2.5 font-medium">Product</th>
              <th className="px-4 py-2.5 font-medium">Severity</th>
              <th className="px-4 py-2.5 font-medium">Stage</th>
              <th className="px-4 py-2.5 font-medium">Gate</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <tr key={c.case_id} className="border-t border-line hover:bg-paper">
                <td className="px-4 py-3 font-mono text-[13px]">{c.tenant_id}</td>
                <td className="px-4 py-3">
                  <Link
                    href={`/cases/${c.case_id}`}
                    className="font-mono text-[13px] text-teal-deep underline-offset-2 hover:underline"
                  >
                    {c.batch_id}
                  </Link>
                </td>
                <td className="px-4 py-3">{c.product_name}</td>
                <td className="px-4 py-3">
                  <SeverityPill value={c.severity} />
                </td>
                <td className="px-4 py-3">
                  <StagePill value={c.desk_stage} />
                </td>
                <td className="px-4 py-3">
                  <GatePill value={c.gate_status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AppShell>
  );
}
