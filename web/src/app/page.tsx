import { AppShell } from "@/components/app-shell";
import { QueueRow } from "@/components/queue-row";
import { listAllCases, liveEngineConfigured } from "@/lib/desk";

export const dynamic = "force-dynamic";

export default function Home() {
  const cases = listAllCases();
  const live = liveEngineConfigured();

  const pendingQp = cases.filter((c) => c.desk_stage === "pending_qp").length;
  const onHold = cases.filter((c) => c.desk_stage === "sterility_hold").length;
  const critical = cases.filter((c) => c.severity === "critical").length;

  return (
    <AppShell current="/">
      <div className="mb-7">
        <h1 className="text-[22px] font-semibold tracking-tight text-ink">
          Case Queue
        </h1>
        <p className="mt-1 text-sm text-ink-muted">
          Active deviation investigations awaiting agent analysis or QP action.
        </p>
      </div>

      {/* Stats */}
      <div className="mb-7 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Total cases" value={cases.length} />
        <Stat label="Pending QP" value={pendingQp} accent="brand" />
        <Stat label="Sterility hold" value={onHold} accent="hold" />
        <Stat label="Critical" value={critical} accent="critical" />
      </div>

      {/* Table card */}
      <section className="overflow-hidden rounded-xl border border-line bg-paper-raised shadow-sm">
        <header className="flex items-center justify-between gap-4 border-b border-line px-5 py-3.5">
          <h2 className="text-sm font-semibold text-ink">
            All cases
            <span className="ml-2 font-normal text-ink-faint">{cases.length}</span>
          </h2>
          <span className="flex items-center gap-2 text-[12px] text-ink-muted">
            <span
              className={`h-1.5 w-1.5 rounded-full ${live ? "bg-minor" : "bg-line-strong"}`}
              aria-hidden
            />
            Agent Runtime {live ? "connected" : "offline"}
          </span>
        </header>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[52rem] text-left">
            <thead>
              <tr className="border-b border-line bg-surface/60">
                <Th>Tenant</Th>
                <Th>Batch</Th>
                <Th>Product</Th>
                <Th>Severity</Th>
                <Th>Stage</Th>
                <Th>Gate</Th>
                <Th>Source</Th>
                <th className="w-8" aria-hidden />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {cases.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-5 py-14 text-center text-sm text-ink-muted">
                    No cases in the queue.
                  </td>
                </tr>
              ) : (
                cases.map((c) => <QueueRow key={c.case_id} c={c} />)
              )}
            </tbody>
          </table>
        </div>
      </section>
    </AppShell>
  );
}

function Th({ children }: { children: string }) {
  return (
    <th className="px-5 py-3 text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
      {children}
    </th>
  );
}

function Stat({
  label,
  value,
  accent = "neutral",
}: {
  label: string;
  value: number;
  accent?: "neutral" | "brand" | "hold" | "critical";
}) {
  const dot =
    accent === "brand" ? "bg-brand" :
    accent === "hold" ? "bg-hold" :
    accent === "critical" ? "bg-critical" :
    "bg-line-strong";

  return (
    <div className="rounded-xl border border-line bg-paper-raised px-5 py-4 shadow-sm">
      <div className="flex items-center gap-2">
        <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden />
        <p className="text-[12px] font-medium text-ink-muted">{label}</p>
      </div>
      <p className="mt-2 text-[28px] font-semibold leading-none tabular text-ink">
        {value}
      </p>
    </div>
  );
}
