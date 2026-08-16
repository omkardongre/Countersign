import { AppShell } from "@/components/app-shell";
import { OpenForm } from "@/components/open-form";

export default function OpenPage() {
  return (
    <AppShell current="/open">
      <h1 className="text-2xl font-semibold tracking-tight">Open case</h1>
      <p className="mt-1 mb-6 max-w-xl text-sm text-ink-muted">
        Capture structured detection fields. Severity is computed in this
        process, not by a model.
      </p>
      <OpenForm />
    </AppShell>
  );
}
