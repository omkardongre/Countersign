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
  if (!gate || (record.desk_stage !== "pending_qp" && !record.qp_confirmation)) return null;

  const recorded = record.qp_confirmation;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const res = await fetch(`/api/cases/${record.classified.case_id}/confirm`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action, qp_name: qpName, rationale }),
      });
      const body = await res.json();
      if (!res.ok) { setError(body.error ?? "Request failed."); return; }
      router.refresh();
    } catch {
      setError("Network error.");
    } finally {
      setPending(false);
    }
  }

  const ACTIONS: { value: QpAction; label: string; desc: string }[] = [
    { value: "confirm",         label: "Confirm",         desc: "Batch meets GMP criteria" },
    { value: "decline",         label: "Decline",         desc: "Batch does not meet criteria" },
    { value: "cannot_confirm",  label: "Cannot confirm",  desc: "Insufficient information" },
  ];

  return (
    <section className="overflow-hidden rounded-xl border border-brand bg-paper-raised shadow-sm">
      <div className="flex items-center gap-3 border-b border-line bg-brand/5 px-5 py-3.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand/10">
          <svg className="h-4 w-4 text-brand" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>
        <div>
          <h2 className="text-[13px] font-semibold text-brand">QP Confirmation</h2>
          <p className="text-[11px] text-ink-muted">
            Personal act of the Qualified Person. This desk does not certify a batch.
          </p>
        </div>
      </div>

      <div className="px-5 py-5">
        {recorded && (
          <div className="mb-5 rounded-lg border border-line bg-surface p-4">
            <div className="flex items-center gap-2 mb-2">
              <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase ${
                recorded.action === "confirm" ? "bg-minor-bg text-minor" : "bg-critical-bg text-critical"
              }`}>
                {recorded.outcome ?? recorded.action}
              </span>
            </div>
            <p className="text-sm font-medium text-ink">{recorded.qp_name}</p>
            <p className="mt-1 text-[13px] text-ink-muted">{recorded.rationale}</p>
            {recorded.rejection_reason && (
              <p className="mt-2 text-[13px] text-critical">{recorded.rejection_reason}</p>
            )}
          </div>
        )}

        {record.desk_stage === "pending_qp" && (
          <form onSubmit={onSubmit} className="space-y-5">
            {/* Action selector */}
            <div>
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                Decision
              </p>
              <div className="grid grid-cols-3 gap-2">
                {ACTIONS.map(({ value, label, desc }) => (
                  <label
                    key={value}
                    className={`flex cursor-pointer flex-col rounded-lg border p-3 transition-colors ${
                      action === value
                        ? "border-brand bg-brand/5 ring-1 ring-brand"
                        : "border-line hover:border-brand/40"
                    }`}
                  >
                    <input
                      type="radio"
                      name="action"
                      value={value}
                      checked={action === value}
                      onChange={() => setAction(value)}
                      className="sr-only"
                    />
                    <span className="text-[13px] font-semibold text-ink">{label}</span>
                    <span className="mt-0.5 text-[11px] text-ink-muted">{desc}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* QP name */}
            <div>
              <label htmlFor="qp_name" className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                QP Name
              </label>
              <input
                id="qp_name"
                required
                minLength={1}
                value={qpName}
                onChange={(e) => setQpName(e.target.value)}
                autoComplete="name"
                placeholder="Dr. A. Example"
                className="w-full rounded-lg border border-line bg-paper px-3 py-2.5 text-sm outline-none transition-colors focus:border-brand focus:ring-1 focus:ring-brand"
              />
            </div>

            {/* Rationale */}
            <div>
              <label htmlFor="rationale" className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                Rationale
              </label>
              <textarea
                id="rationale"
                required
                minLength={1}
                rows={3}
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                placeholder="Basis for this decision..."
                className="w-full rounded-lg border border-line bg-paper px-3 py-2.5 text-sm outline-none transition-colors focus:border-brand focus:ring-1 focus:ring-brand"
              />
            </div>

            {gate.status === "not_ready" && action === "confirm" && (
              <div className="flex items-start gap-2 rounded-lg border border-warn-bg bg-warn-bg px-3 py-2.5">
                <svg className="mt-0.5 h-4 w-4 flex-shrink-0 text-warn" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                </svg>
                <p className="text-[13px] text-warn">
                  Annex 16 pack is not ready. Submitting Confirm will be recorded as rejected.
                </p>
              </div>
            )}

            {error && <p className="text-sm text-critical">{error}</p>}

            <button
              type="submit"
              disabled={pending}
              className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-dark disabled:opacity-50"
            >
              {pending ? (
                <>
                  <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Recording…
                </>
              ) : (
                "Record Confirmation"
              )}
            </button>
          </form>
        )}
      </div>
    </section>
  );
}
