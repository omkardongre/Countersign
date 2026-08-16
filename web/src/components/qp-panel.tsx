"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { CaseDeskRecord, QpAction } from "@/lib/types";

export function QpPanel({ record }: { record: CaseDeskRecord }) {
  const router = useRouter();
  const [action, setAction] = useState<QpAction>("confirm");
  const [qpName, setQpName] = useState("");
  const [rationale, setRationale] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const gate = record.release_gate;
  if (!gate || (record.desk_stage !== "pending_qp" && !record.qp_confirmation)) {
    return null;
  }

  const recorded = record.qp_confirmation;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const res = await fetch(`/api/cases/${record.classified.case_id}/confirm`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          action,
          qp_name: qpName,
          rationale,
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        setError(body.error ?? "Request failed.");
        return;
      }
      router.refresh();
    } catch {
      setError("Network error.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="border border-teal-deep bg-paper-raised">
      <header className="border-b border-line px-4 py-2.5">
        <h2 className="text-[13px] font-semibold tracking-wide uppercase text-teal-deep">
          QP confirmation
        </h2>
        <p className="mt-1 text-[13px] text-ink-muted">
          Personal act. Not a finished-product certification. Confirm is refused
          while the pack is not_ready.
        </p>
      </header>
      <div className="px-4 py-4">
        {recorded && (
          <div className="mb-4 border border-line bg-paper px-3 py-2 text-sm">
            <p className="font-mono text-[12px] uppercase">{recorded.outcome}</p>
            <p className="mt-1">
              {recorded.qp_name} · {recorded.action}
            </p>
            <p className="mt-1 text-ink-muted">{recorded.rationale}</p>
            {recorded.rejection_reason && (
              <p className="mt-2 text-critical">{recorded.rejection_reason}</p>
            )}
          </div>
        )}
        {record.desk_stage === "pending_qp" && (
          <form onSubmit={onSubmit} className="space-y-3">
            <fieldset className="flex flex-wrap gap-3 text-sm">
              <legend className="sr-only">Action</legend>
              {(
                [
                  ["confirm", "Confirm"],
                  ["decline", "Decline"],
                  ["cannot_confirm", "Cannot confirm"],
                ] as const
              ).map(([value, label]) => (
                <label key={value} className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="action"
                    value={value}
                    checked={action === value}
                    onChange={() => setAction(value)}
                  />
                  {label}
                </label>
              ))}
            </fieldset>
            <div>
              <label htmlFor="qp_name" className="block text-[12px] uppercase text-ink-muted">
                QP name
              </label>
              <input
                id="qp_name"
                required
                minLength={1}
                value={qpName}
                onChange={(e) => setQpName(e.target.value)}
                autoComplete="name"
                className="mt-1 w-full border border-line bg-paper px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label htmlFor="rationale" className="block text-[12px] uppercase text-ink-muted">
                Rationale
              </label>
              <textarea
                id="rationale"
                required
                minLength={1}
                rows={3}
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                className="mt-1 w-full border border-line bg-paper px-3 py-2 text-sm"
              />
            </div>
            {gate.status === "not_ready" && action === "confirm" && (
              <p className="text-[13px] text-warn">
                This pack is not_ready. Submitting Confirm will be recorded as
                rejected.
              </p>
            )}
            {error && <p className="text-sm text-critical">{error}</p>}
            <button
              type="submit"
              disabled={pending}
              className="bg-teal-deep px-4 py-2 text-sm text-paper-raised disabled:opacity-50"
            >
              {pending ? "Recording…" : "Record confirmation"}
            </button>
          </form>
        )}
      </div>
    </section>
  );
}
