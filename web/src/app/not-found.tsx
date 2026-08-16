import { AppShell } from "@/components/app-shell";

export default function NotFound() {
  return (
    <AppShell>
      <h1 className="text-2xl font-semibold">Case not found</h1>
      <p className="mt-2 text-sm text-ink-muted">
        Intake-opened cases live in this process only and reset on restart.
      </p>
    </AppShell>
  );
}
