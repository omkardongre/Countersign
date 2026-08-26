import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { OpenForm } from "@/components/open-form";
import { liveEngineConfigured } from "@/lib/desk";

export const dynamic = "force-dynamic";

export default function OpenPage() {
  return (
    <AppShell current="/open">
      <div className="mx-auto max-w-3xl">
        <Link
          href="/"
          className="mb-5 inline-flex items-center gap-1.5 text-[13px] text-ink-muted transition-colors hover:text-ink"
        >
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
          </svg>
          Case Queue
        </Link>

        <div className="mb-7">
          <h1 className="text-[22px] font-semibold tracking-tight text-ink">
            Open a case
          </h1>
          <p className="mt-1 text-sm text-ink-muted">
            Capture structured detection fields. Severity is computed
            deterministically in this process, never by a model.
          </p>
        </div>

        <OpenForm liveEngine={liveEngineConfigured()} />
      </div>
    </AppShell>
  );
}
