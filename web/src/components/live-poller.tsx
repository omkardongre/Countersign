"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

const POLL_INTERVAL_MS = 4000;
/** Stop polling after this long so an idle tab does not call Runtime forever. */
const MAX_POLL_MS = 15 * 60 * 1000;

/**
 * Polls a live case's BFF route and refreshes the server component when the
 * graph advances.
 *
 * Polling, not SSE: a single Agent Runtime streaming response is capped at 15
 * minutes (`final_reseach/ARCHITECTURE.md` §4.4) and the sterility hold runs
 * for days, so the desk must observe session state rather than hold a
 * connection. Each `GET /api/cases/[caseId]` re-reads the Runtime session and
 * re-projects it (`lib/live/live-store.ts` `pollLiveCase`).
 */
export function LivePoller({
  caseId,
  initialStage,
}: {
  caseId: string;
  /**
   * The server-rendered `desk_stage` at the time this component mounted.
   * Without this, the first client-side poll sets itself as the baseline
   * signature with nothing to compare against — if the graph had already
   * advanced between the server render and the first poll (routine on a
   * page load a few seconds after a case reached its hold), that advance is
   * silently swallowed and the desk never calls `router.refresh()`, leaving
   * the stale server-rendered stage on screen until a manual reload.
   */
  initialStage: string;
}) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  const signatureRef = useRef<string | null>(`stage:${initialStage}`);
  // Set inside the effect, not during render: `Date.now()` is impure and a
  // render-time call can be discarded or repeated by React.
  const startedAtRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    startedAtRef.current = Date.now();

    async function tick() {
      if (cancelled) return;
      if (Date.now() - (startedAtRef.current ?? Date.now()) > MAX_POLL_MS) {
        setStopped(true);
        return;
      }
      try {
        const res = await fetch(`/api/cases/${caseId}`, { cache: "no-store" });
        const body = await res.json();
        if (cancelled) return;
        if (!res.ok) {
          setError(body.error ?? "Poll failed.");
          return;
        }
        setError(null);
        // Refresh only when something the desk renders actually changed,
        // so an unchanged parked case does not re-render every 4 seconds.
        // The baseline starts from the server-rendered stage (see
        // `initialStage`), not from this component's first poll, so a
        // graph advance that happened before the first poll still triggers
        // a refresh instead of being silently missed.
        const signature = JSON.stringify({
          stage: body.desk_stage,
          gate: body.release_gate?.status ?? null,
          branches: body.branches?.length ?? 0,
          findings: body.findings?.length ?? 0,
          draft: Boolean(body.draft),
          qp: body.qp_confirmation?.outcome ?? null,
        });
        if (signatureRef.current !== signature) {
          router.refresh();
        }
        signatureRef.current = signature;
      } catch {
        if (!cancelled) setError("Network error while polling.");
      }
    }

    void tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [caseId, router]);

  return (
    <p
      aria-live="polite"
      className="mb-3 border border-line bg-paper-raised px-3 py-1.5 font-mono text-[12px] text-ink-muted"
    >
      {error ? (
        <span className="text-critical">Live poll: {error}</span>
      ) : stopped ? (
        "Live poll stopped after 15 minutes. Reload to resume watching."
      ) : (
        "Polling Agent Runtime session · no SSE held across the wait"
      )}
    </p>
  );
}
